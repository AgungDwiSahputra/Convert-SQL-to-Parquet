#!/usr/bin/env python3
"""
Mesin ekspor SQL Server 2012 -> Parquet (multi-job, chunking, dataset AI/ML).

File ini adalah KODE INTI. Kamu jarang perlu menyentuhnya.
Daftar tabel/query yang mau diekspor ada di file terpisah: jobs.py

SQL Server 2012 tidak punya dukungan Parquet native. Pola:
    tarik data via pyodbc (streaming, fetchmany) -> tulis Parquet per-batch dengan ParquetWriter.

Schema = CAMPUR (auto default, manual kalau perlu):
  - Tipe kolom dideteksi OTOMATIS dari metadata SQL Server (cursor.description).
  - decimal/numeric -> decimal128(p,s) (presisi terjaga, BUKAN float).
  - datetime/datetime2 -> timestamp[us]; bit -> boolean; uniqueidentifier -> string.
  - Override manual per kolom lewat field 'overrides' di tiap Job (lihat jobs.py).

Cara pakai:
    python exporter.py                              # jalankan utk TARGET_DATE default (jobs.py)
    python exporter.py --date 2026-07-15             # jalankan utk 1 tanggal tertentu
    python exporter.py --start-date 2026-07-01 --end-date 2026-07-15   # jalankan utk range tanggal
    python exporter.py --only awl tmat               # hanya job tertentu (bisa digabung dgn tanggal di atas)
    python exporter.py --only "bronze_hms_*"         # wildcard: semua job yang namanya cocok pola (kutip di shell)
    python exporter.py --list                        # lihat daftar job
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

Dependensi:
    pip install pyodbc pyarrow boto3 python-dotenv
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
import pyodbc
import pyarrow as pa
import pyarrow.parquet as pq

# Konfigurasi dari file terpisah -------------------------------------------------
from config import (
    CONN_STR, BATCH_SIZE, COMPRESSION, OUTPUT_DIR,
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME,
)
from jobs import JOBS, TARGET_DATE, build_jobs


# ---------------------------------------------------------------------------
# AUTO-DETECT SCHEMA - dari metadata pyodbc -> tipe Arrow
# ---------------------------------------------------------------------------

def arrow_type_from_description(col_desc) -> pa.DataType:
    """
    Petakan satu entri cursor.description -> tipe Arrow.
    cursor.description item: (name, type_code, display_size, internal_size, precision, scale, null_ok)
    """
    import datetime as _dt
    import decimal as _dec

    _name, type_code, _disp, _int, precision, scale, _null = col_desc

    if type_code is _dec.Decimal:
        p = precision if precision and precision > 0 else 38
        s = scale if scale is not None and scale >= 0 else 0
        if s > p:
            p = s
        return pa.decimal128(p, s)
    if type_code is bool:
        return pa.bool_()
    if type_code is int:
        return pa.int64()
    if type_code is float:
        return pa.float64()
    if type_code is _dt.datetime:
        return pa.timestamp("us")     # asumsi WIB; SQL Server tidak simpan timezone
    if type_code is _dt.date:
        return pa.date32()
    if type_code is _dt.time:
        return pa.time64("us")
    if type_code in (bytes, bytearray, memoryview):
        return pa.binary()
    return pa.string()   # str, uniqueidentifier, dll.


def build_schema(cursor, overrides: dict) -> pa.Schema:
    """Bangun schema Arrow dari cursor.description, terapkan override manual per nama kolom."""
    fields = []
    for col in cursor.description:
        col_name = col[0]
        dtype = overrides.get(col_name) or arrow_type_from_description(col)
        fields.append(pa.field(col_name, dtype))
    return pa.schema(fields)


# ---------------------------------------------------------------------------
# LOGIKA EKSPOR
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parquet-export")


def rows_to_table(rows: list, schema: pa.Schema, ingested_at: datetime | None = None) -> pa.Table:
    """
    Baris pyodbc -> pyarrow.Table sesuai schema. NULL (None) dibiarkan null.
    Kalau ingested_at diisi, field TERAKHIR di `schema` diasumsikan kolom audit
    '_ingested_at' (lihat run_job()) -- nilainya di-broadcast SAMA untuk semua baris,
    satu timestamp per batch tulis (bukan per baris), karena ini metadata pipeline
    (kapan batch ini ditulis), bukan data yang datang dari SQL Server.
    """
    data_fields = pa.schema(list(schema)[:-1]) if ingested_at is not None else schema
    columnar = list(zip(*rows)) if rows else [[] for _ in data_fields]
    arrays = [pa.array(list(vals), type=f.type) for f, vals in zip(data_fields, columnar)]
    if ingested_at is not None:
        arrays.append(pa.array([ingested_at] * len(rows), type=schema[-1].type))
    return pa.Table.from_arrays(arrays, schema=schema)


def job_matches(name: str, patterns: list[str]) -> bool:
    """
    True kalau `name` sama persis dengan salah satu pattern, ATAU cocok pola wildcard
    (fnmatch: '*' = apa saja, '?' = satu karakter). Dipakai --only supaya bisa menjalankan
    satu grup job sekaligus lewat prefix nama, mis. 'bronze_hms_*' (bukan cuma nama persis).
    """
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def resolve_output(output: str, date_suffix: str | None = None) -> str:
    """
    Gabungkan OUTPUT_DIR + nama file bila output bukan path absolut.
    date_suffix (mis. '20260701') disisipkan sebelum ekstensi -- dipakai waktu
    mode range supaya file lokal per-tanggal tidak saling menimpa.
    """
    if date_suffix:
        root, ext = os.path.splitext(output)
        output = f"{root}_{date_suffix}{ext}"
    if os.path.isabs(output):
        return output
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, output)


def _advance_to_resultset_with_columns(cursor):
    """
    SP yang menjalankan EXEC sp_ETL_Load... lalu SELECT bisa menghasilkan beberapa
    resultset (atau resultset tanpa kolom dari statement non-SELECT). Lompati resultset
    yang cursor.description-nya None sampai ketemu yang punya kolom (SELECT sesungguhnya).
    Mengembalikan True bila ketemu resultset berkolom, False bila habis.
    """
    while cursor.description is None:
        if not cursor.nextset():
            return False
    return True


def run_job(conn: pyodbc.Connection, job, batch_size: int, compression, s3_client=None,
            date_suffix: str | None = None, skip_local: bool = False) -> int:
    started = datetime.now()
    tag = job.name if date_suffix is None else f"{job.name}@{date_suffix}"

    write_to_s3 = job.s3_key is not None and s3_client is not None
    # skip_local cuma efektif kalau ada tujuan S3 -- kalau tidak, tetap simpan lokal
    # supaya hasil query (yang juga memicu ETL Load di production) tidak hilang percuma.
    use_local_file = not (skip_local and write_to_s3)

    if use_local_file:
        out_path = resolve_output(job.output, date_suffix)
        sink = out_path
    else:
        out_path = None
        sink = io.BytesIO()
        log.info("[%s] --skip-local: parquet ditulis di memori, langsung upload ke S3.", tag)

    if skip_local and use_local_file and not write_to_s3:
        log.warning("[%s] --skip-local diminta tapi job ini tidak ada tujuan S3 (s3_key kosong / S3 nonaktif) -- "
                    "tetap disimpan lokal.", tag)

    log.info("[%s] menjalankan query...", tag)

    cursor = conn.cursor()
    # Params dikirim terpisah (aman dari SQL injection). Urutan sesuai tanda '?' di query.
    if job.params:
        cursor.execute(job.query, job.params)
    else:
        cursor.execute(job.query)

    # SP dengan EXEC + SELECT: lompati resultset non-SELECT sampai ketemu yang berkolom.
    if not _advance_to_resultset_with_columns(cursor):
        log.warning("[%s] query tidak menghasilkan resultset berkolom (tidak ada data). Dilewati.",
                    tag)
        return 0

    schema = build_schema(cursor, job.overrides)
    data_field_names = [f.name for f in schema]  # kolom asli dari SQL Server, sebelum _ingested_at
    # Satu timestamp UTC per JOB RUN (bukan per-row/per-batch) -- konsisten dgn desain
    # overwrite-per-partition: re-run yang sama akan menimpa dengan _ingested_at baru.
    ingested_at = datetime.now(timezone.utc)
    schema = schema.append(pa.field("_ingested_at", pa.timestamp("us", tz="UTC")))
    log.info("[%s] schema: %s", tag, ", ".join(f"{f.name}:{f.type}" for f in schema))

    total = 0
    first_row = None
    writer: pq.ParquetWriter | None = None
    try:
        writer = pq.ParquetWriter(sink, schema, compression=compression)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            if first_row is None:
                first_row = dict(zip(data_field_names, rows[0]))
            writer.write_table(rows_to_table(rows, schema, ingested_at))
            total += len(rows)
            log.info("[%s]   +%s baris (total %s)", tag, f"{len(rows):,}", f"{total:,}")
    finally:
        if writer is not None:
            writer.close()

    elapsed = (datetime.now() - started).total_seconds()
    if use_local_file:
        size_mb = os.path.getsize(out_path) / (1024 * 1024) if os.path.exists(out_path) else 0
        location = out_path
    else:
        size_mb = sink.getbuffer().nbytes / (1024 * 1024)
        location = "(memori, tidak disimpan lokal)"
    log.info("[%s] SELESAI: %s baris -> %s (%.1f MB) dalam %.1fs",
             tag, f"{total:,}", location, size_mb, elapsed)
    if job.labels:
        log.info("[%s] kolom label (target model): %s", tag, job.labels)

    if total > 0 and write_to_s3:
        key = job.s3_key(first_row)
        log.info("[%s] upload ke s3://%s/%s ...", tag, S3_BUCKET_NAME, key)
        if use_local_file:
            s3_client.upload_file(out_path, S3_BUCKET_NAME, key)
        else:
            sink.seek(0)
            s3_client.upload_fileobj(sink, S3_BUCKET_NAME, key)
        log.info("[%s] upload S3 selesai.", tag)
    elif total > 0 and job.s3_key and s3_client is None:
        log.warning("[%s] punya s3_key tapi upload dilewati (--skip-s3 atau kredensial S3 belum lengkap).",
                    tag)

    return total


def main() -> int:
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
                   help="jalankan untuk 1 tanggal tertentu (override TARGET_DATE di jobs.py)")
    p.add_argument("--start-date", metavar="YYYY-MM-DD", help="tanggal awal range (pakai bareng --end-date)")
    p.add_argument("--end-date", metavar="YYYY-MM-DD", help="tanggal akhir range, inklusif (pakai bareng --start-date)")
    args = p.parse_args()

    if args.list:
        for j in JOBS:
            s3_note = " (+ S3)" if j.s3_key else ""
            print(f"  {j.name:<14} -> {j.output}{s3_note}")
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
            start = end = datetime.strptime(TARGET_DATE, "%Y-%m-%d").date()
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
    all_names = {j.name for j in JOBS}
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

    s3_client = None
    if not args.skip_s3:
        if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and S3_BUCKET_NAME:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
        else:
            log.warning("Kredensial S3 belum lengkap di .env, upload S3 dilewati untuk semua job.")

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
                jobs_today = build_jobs(date_str)
                if wanted_patterns:
                    jobs_today = [j for j in jobs_today if job_matches(j.name, wanted_patterns)]
                for job in jobs_today:
                    if is_range and not job.needs_date and job.name in date_independent_done:
                        continue
                    date_suffix = d.strftime("%Y%m%d") if (is_range and job.needs_date) else None
                    try:
                        run_job(conn, job, args.batch, comp, s3_client, date_suffix, args.skip_local)
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
