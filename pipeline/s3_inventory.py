"""
INVENTARIS S3 -- tanggal data yang benar-benar ada di S3 per job. freshness.py memakainya bersama dq_results
untuk kesegaran & celah data.

Kenapa perlu: dq_results hanya berisi run yang dicatat pipeline ini (sejak fitur DQ). Upload sebelum itu atau
dari CLI dengan jobs.db lain tidak tercatat -- tanpa inventaris, riwayat job "dimulai" dari catatan pertamanya
sehingga celah sebelum tanggal itu tidak terlihat.

Cara kerja: untuk tiap job AKTIF berirama harian/mingguan/bulanan dengan layout S3 harian/mingguan/bulanan, folder
datasetnya di-list (ListObjectsV2, 1 request per 1.000 objek) dan tanggal partisinya dicatat di tabel
s3_inventory (diganti penuh per job tiap pindai). Partisi mingguan (recorded_week=W{n}, nilai dari SQL Server)
dicatat sebagai 7 hari minggu itu menurut konvensi minggu PZO (periods.pzo_week); partisi bulanan
(recorded_month=MM tanpa partisi di bawahnya) sebagai seluruh hari bulan itu. Master (snapshot_date=)
tidak dipindai: foldernya digeser maju oleh pipeline/snapshots.py, bukan riwayat per tanggal.

Batasan: hari 0 baris tidak membuat file di S3 (export.run_job hanya upload kalau ada baris), jadi tanggal yang
tidak ada di S3 DAN tidak tercatat di dq_results bisa berarti belum ter-load ATAU memang kosong. Run ulang
tanggal itu menutup celahnya (hasil 0 baris tercatat di dq_results).

Hanya MEMBACA S3 (butuh izin s3:ListBucket). Dijalankan sekali sehari oleh thread scheduler worker (run_daily,
setelah s3inv.scan_time WIB), manual dari halaman Pengaturan, atau `python manage.py scan-s3`.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from pipeline.app_settings import get_settings, set_settings
from pipeline.config import S3_BUCKET_NAME
from pipeline.freshness import GAP_CADENCES
from pipeline.job_store import JobRow, load_job_rows, utc_now, write_tx
from pipeline.job_templates import s3_list_prefix, s3_target_info
from pipeline.periods import period_bounds, pzo_week_start
from pipeline.scheduler import WIB

LAYOUTS = ("daily", "weekly", "monthly")
_PATTERNS = {
    "daily": re.compile(r"^recorded_year=(\d{4})/recorded_month=(\d{1,2})/recorded_day=(\d{1,2})/[^/]+\.parquet$"),
    "weekly": re.compile(r"^recorded_year=(\d{4})/recorded_month=(\d{1,2})/recorded_week=W(\d{1,2})/[^/]+\.parquet$"),
    "monthly": re.compile(r"^recorded_year=(\d{4})/recorded_month=(\d{1,2})/[^/]+\.parquet$"),
}
# Error S3 yang membuat SEMUA job pasti gagal -- pindai dihentikan. Pesan sendiri, BUKAN pesan asli AWS
# (pesan asli memuat ARN user IAM, nomor akun, dan nama bucket).
_FATAL = {
    "AccessDenied": "Akses ditolak S3: akun AWS di .env perlu izin s3:ListBucket pada bucket/prefix data.",
    "AllAccessDisabled": "Akses ditolak S3: akun AWS di .env perlu izin s3:ListBucket pada bucket/prefix data.",
    "InvalidAccessKeyId": "Kunci akses AWS di .env tidak dikenal.",
    "SignatureDoesNotMatch": "Kunci rahasia AWS di .env tidak cocok.",
    "NoSuchBucket": "Bucket S3 di .env tidak ditemukan.",
}


def partition_dates(layout: str, rel_key: str) -> list[date]:
    """Key relatif terhadap folder dataset -> tanggal data yang diwakilinya ([] kalau bukan file partisi)."""
    m = _PATTERNS[layout].match(rel_key)
    if not m:
        return []
    y, mo, *n = (int(x) for x in m.groups())
    try:
        if layout == "daily":
            return [date(y, mo, n[0])]
        if layout == "monthly":
            start, end = period_bounds("month", date(y, mo, 1))
            return [start + timedelta(days=i) for i in range((end - start).days + 1)]
        monday = pzo_week_start(y, mo, n[0])
    except ValueError:
        return []
    return [monday + timedelta(days=i) for i in range(7)]


def scan_target(row: JobRow) -> tuple[str, str] | None:
    """(layout, prefix) yang dipindai untuk job ini, atau None (master / tanpa S3 / irama snapshot)."""
    info = s3_target_info(row.template, row.args)
    if row.effective_cadence not in GAP_CADENCES or not info or info.get("layout") not in LAYOUTS:
        return None
    prefix = s3_list_prefix(row.template, row.args)
    return (info["layout"], prefix) if prefix else None


def list_job_dates(s3, layout: str, prefix: str) -> dict[str, tuple[str, int, str]]:
    """target_date -> (key file terbaru, total ukuran byte, last_modified UTC) dari isi folder dataset di S3."""
    found: dict[str, tuple[str, int, str]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET_NAME, Prefix=prefix):
        for obj in page.get("Contents", []):
            modified = obj["LastModified"].astimezone(timezone.utc).isoformat(timespec="seconds")
            for d in partition_dates(layout, obj["Key"][len(prefix):]):
                key, size, last = found.get(d.isoformat(), (obj["Key"], 0, modified))
                if modified >= last:
                    key, last = obj["Key"], modified
                found[d.isoformat()] = (key, size + obj["Size"], last)
    return found


def _error_code(e: Exception) -> str:
    return (getattr(e, "response", None) or {}).get("Error", {}).get("Code") or type(e).__name__


def scan(conn: sqlite3.Connection, s3, rows: list[JobRow] | None = None, by: str = "worker") -> dict:
    """
    Pindai S3 untuk job aktif (atau `rows`) dan ganti isi s3_inventory per job yang berhasil. Return ringkasan
    {at, by, jobs, dates, errors[{job_name, error}], error} -- juga disimpan di settings s3inv.last_result.
    """
    rows = load_job_rows() if rows is None else rows
    targets = [(r, t) for r in rows if (t := scan_target(r))]
    result = {"at": utc_now(), "by": by, "jobs": 0, "targets": len(targets), "dates": 0, "errors": [], "error": None}
    if s3 is None:
        result["error"] = "Kredensial S3 belum lengkap di .env server."
        targets = []
    for row, (layout, prefix) in targets:
        try:
            found = list_job_dates(s3, layout, prefix)
        except Exception as e:  # noqa: BLE001 -- satu job gagal tidak menghentikan yang lain
            code = _error_code(e)
            if code in _FATAL:
                result["error"] = _FATAL[code]
                break
            result["errors"].append({"job_name": row.name, "error": f"Gagal membaca S3 ({code})."})
            continue
        now = utc_now()
        with write_tx(conn):
            conn.execute("DELETE FROM s3_inventory WHERE job_name = ?", (row.name,))
            conn.executemany("INSERT INTO s3_inventory (job_name, target_date, s3_key, size_bytes, last_modified, "
                             "scanned_at) VALUES (?, ?, ?, ?, ?, ?)",
                             [(row.name, d, k, size, modified, now) for d, (k, size, modified) in found.items()])
        result["jobs"] += 1
        result["dates"] += len(found)
    conn.execute("DELETE FROM s3_inventory WHERE job_name NOT IN (SELECT name FROM jobs)")
    set_settings(conn, {"s3inv.last_result": result}, by)
    return result


def run_daily(conn: sqlite3.Connection, s3_factory, now: datetime | None = None) -> dict | None:
    """Pindai harian (thread scheduler worker). None kalau nonaktif / belum waktunya / sudah hari ini."""
    s = get_settings(conn, "s3inv.")
    if not s["s3inv.enabled"]:
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(WIB)
    hh, mm = (int(x) for x in s["s3inv.scan_time"].split(":"))
    if now.time() < time(hh, mm) or s["s3inv.last_date"] == now.date().isoformat():
        return None
    set_settings(conn, {"s3inv.last_date": now.date().isoformat()}, "worker")  # tandai dulu: gagal pun tidak diulang
    return scan(conn, s3_factory(), by="worker")

