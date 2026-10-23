#!/usr/bin/env python3
"""
CLI ekspor SQL Server 2012 -> Parquet (multi-job, chunking, dataset AI/ML).

File ini hanya antarmuka command line. Mesin ekspornya ada di pipeline/export.py; daftar
tabel/query yang diekspor ada di database job (jobs.db, lihat pipeline/job_store.py) dan
dikelola lewat Web UI.

Cara pakai:
    python exporter.py                              # jalankan utk tanggal default (KEMARIN, kalender WIB)
    python exporter.py --date 2026-07-15             # jalankan utk 1 tanggal tertentu
    python exporter.py --start-date 2026-07-01 --end-date 2026-07-15   # jalankan utk range tanggal
    python exporter.py --only awl tmat               # hanya job tertentu (bisa digabung dgn tanggal di atas)
    python exporter.py --only "bronze_hms_*"         # wildcard: semua job yang namanya cocok pola (kutip di shell)
    python exporter.py --list                        # lihat daftar job (termasuk yang nonaktif)
    python exporter.py --stop-on-error
    python exporter.py --skip-s3                     # cuma simpan lokal, tidak upload ke S3
    python exporter.py --skip-local                  # cuma upload ke S3, tidak simpan ke folder output/ lokal
                                                       # (job tanpa tujuan S3 tetap disimpan lokal, supaya data tidak hilang)

Catatan mode range (--start-date/--end-date):
    - Job harian (butuh @TargetDate) dijalankan SEKALI PER TANGGAL dalam range,
      dan file Parquet lokalnya diberi sufiks tanggal (mis. gold_awl_readings_20260701.parquet)
      supaya tidak saling menimpa. S3 key sudah unik per tanggal sejak awal.
    - Job master (dev, estate, block, dst -- tidak butuh @TargetDate) HANYA
      dijalankan sekali untuk seluruh range (datanya tidak tergantung tanggal).
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from datetime import datetime, timedelta

import pyodbc

from pipeline.config import BATCH_SIZE, COMPRESSION, CONN_STR
from pipeline.export import log, make_s3_client, run_job, setup_logging
from pipeline.job_store import (
    JobStoreError, build_jobs, connect, default_target_date, init_db, load_job_rows, mark_s3_uploaded,
)


def job_matches(name: str, patterns: list[str]) -> bool:
    """
    True kalau `name` sama persis dengan salah satu pattern, ATAU cocok pola wildcard
    (fnmatch: '*' = apa saja, '?' = satu karakter). Dipakai --only supaya bisa menjalankan
    satu grup job sekaligus lewat prefix nama, mis. 'bronze_hms_*' (bukan cuma nama persis).
    """
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser(description="Ekspor beberapa tabel SQL Server -> Parquet.")
    p.add_argument("--only", nargs="*", metavar="NAME",
                   help="jalankan hanya job yang cocok -- nama persis ATAU pola wildcard "
                        "(* dan ?), mis. --only \"bronze_hms_*\" untuk semua job bronze HMS "
                        "sekaligus tanpa menyebut satu-satu")
    p.add_argument("-b", "--batch", type=int, default=BATCH_SIZE, help="baris per batch")
    p.add_argument("-c", "--compression", default=COMPRESSION,
                   choices=["zstd", "snappy", "gzip", "none"], help="metode kompresi")
    p.add_argument("--stop-on-error", action="store_true", help="berhenti di job/tanggal pertama yang gagal")
    p.add_argument("--list", action="store_true", help="tampilkan daftar job lalu keluar")
    p.add_argument("--skip-s3", action="store_true", help="jangan upload ke S3, simpan lokal saja")
    p.add_argument("--skip-local", action="store_true",
                   help="jangan simpan ke folder output/ lokal (langsung upload ke S3 dari memori); "
                        "job tanpa tujuan S3 tetap disimpan lokal")
    p.add_argument("--date", metavar="YYYY-MM-DD",
                   help="jalankan untuk 1 tanggal tertentu (default: kemarin, kalender WIB)")
    p.add_argument("--start-date", metavar="YYYY-MM-DD", help="tanggal awal range (pakai bareng --end-date)")
    p.add_argument("--end-date", metavar="YYYY-MM-DD", help="tanggal akhir range, inklusif (pakai bareng --start-date)")
    args = p.parse_args()

    # Daftar job dibaca SEKALI di awal run -- semua tanggal di mode range memakai snapshot yang sama.
    # init_db dulu: skema database ikut dinaikkan walau web/worker belum pernah dijalankan di mesin ini.
    try:
        db = connect()
        try:
            init_db(db)
        finally:
            db.close()
        all_rows = load_job_rows(enabled_only=False)
    except JobStoreError as e:
        log.error("%s", e)
        return 1
    job_rows = [r for r in all_rows if r.enabled]

    if args.list:
        for r, j in zip(all_rows, build_jobs(default_target_date(), all_rows)):
            s3_note = " (+ S3)" if j.s3_key else ""
            off_note = "  [nonaktif]" if not r.enabled else ""
            print(f"  {j.name:<14} -> {j.output}{s3_note}{off_note}")
        return 0

    if args.date and (args.start_date or args.end_date):
        log.error("Pilih salah satu: --date ATAU --start-date/--end-date, jangan dua-duanya.")
        return 1
    if bool(args.start_date) != bool(args.end_date):
        log.error("--start-date dan --end-date harus dipakai bersamaan.")
        return 1

    try:
        if args.start_date:
            start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
            end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        elif args.date:
            start = end = datetime.strptime(args.date, "%Y-%m-%d").date()
        else:
            start = end = datetime.strptime(default_target_date(), "%Y-%m-%d").date()
    except ValueError as e:
        log.error("Format tanggal salah, pakai YYYY-MM-DD: %s", e)
        return 1
    if end < start:
        log.error("--end-date harus >= --start-date.")
        return 1

    date_list = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    is_range = len(date_list) > 1

    # Validasi --only terhadap daftar nama job (tidak berubah antar-tanggal). Tiap item
    # boleh nama persis ATAU pola wildcard (job_matches, fnmatch: * dan ?) -- jadi satu
    # pola seperti 'bronze_hms_*' bisa mewakili banyak job sekaligus.
    all_names = {r.name for r in job_rows}
    wanted_patterns = args.only or None
    if wanted_patterns:
        matched_names = {n for n in all_names if job_matches(n, wanted_patterns)}
        unmatched_patterns = [p for p in wanted_patterns
                               if not any(fnmatch.fnmatch(n, p) for n in all_names)]
        if unmatched_patterns:
            log.warning("Pola/nama --only tidak cocok job manapun (diabaikan): %s",
                        ", ".join(unmatched_patterns))
        if not matched_names:
            log.error("Tidak ada job cocok dengan --only. Gunakan --list untuk melihat pilihan.")
            return 1

    comp = None if args.compression == "none" else args.compression

    s3_client = make_s3_client(args.skip_s3)

    if is_range:
        log.info("Mode range: %s s/d %s (%d tanggal). Job master (tanpa @TargetDate) hanya dijalankan sekali.",
                  date_list[0], date_list[-1], len(date_list))

    log.info("Menghubungkan ke SQL Server...")
    ok, failed = 0, 0
    date_independent_done: set[str] = set()
    stop_all = False
    try:
        with pyodbc.connect(CONN_STR) as conn:
            for d in date_list:
                if stop_all:
                    break
                date_str = d.strftime("%Y-%m-%d")
                jobs_today = build_jobs(date_str, job_rows)
                if wanted_patterns:
                    jobs_today = [j for j in jobs_today if job_matches(j.name, wanted_patterns)]
                for job in jobs_today:
                    if is_range and not job.needs_date and job.name in date_independent_done:
                        continue
                    date_suffix = d.strftime("%Y%m%d") if (is_range and job.needs_date) else None
                    try:
                        result = run_job(conn, job, args.batch, comp, s3_client, date_suffix, args.skip_local)
                        if result.s3_key:
                            mark_s3_uploaded(job.name)  # path S3 sudah dipakai -> dikunci di Web UI
                        ok += 1
                        if not job.needs_date:
                            date_independent_done.add(job.name)
                    except Exception as e:  # noqa: BLE001
                        failed += 1
                        log.error("[%s] (%s) GAGAL: %s", job.name, date_str, e)
                        if args.stop_on_error:
                            log.error("Berhenti karena --stop-on-error.")
                            stop_all = True
                            break
    except pyodbc.Error as e:
        log.error("Kesalahan koneksi SQL Server: %s", e)
        return 1

    log.info("Ringkasan: %s job sukses, %s job gagal.", ok, failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
