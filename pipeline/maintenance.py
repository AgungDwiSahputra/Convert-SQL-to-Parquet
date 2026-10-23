"""
PEMELIHARAAN HARIAN -- dijalankan worker.py sekali per hari (kalender WIB):
  1. backup jobs.db (API backup SQLite, aman walau sedang dipakai) ke BACKUP_DIR, simpan BACKUP_KEEP terakhir;
  2. hapus log run (logs/runs/run_*.log) & log worker (logs/worker_*.log) yang lebih tua dari
     LOG_RETENTION_DAYS. Log CLI (logs/exporter_*.log) TIDAK disentuh. Riwayat run di database tetap utuh;
  3. hapus session login yang sudah kedaluwarsa.
"""

from __future__ import annotations

import glob
import os
import sqlite3
import time
from datetime import datetime, timezone

from pipeline.app_settings import get_settings, set_settings
from pipeline.config import BACKUP_DIR, BACKUP_KEEP, LOG_DIR, LOG_RETENTION_DAYS
from pipeline.job_store import utc_now
from pipeline.run_store import RUN_LOG_DIR
from pipeline.scheduler import WIB


def backup_db(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"jobs_{now.astimezone(WIB):%Y%m%d_%H%M%S}.db")
    dst = sqlite3.connect(path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "jobs_*.db")))
    for old in backups[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        os.remove(old)
    return path


def prune_logs(now_ts: float | None = None) -> int:
    cutoff = (now_ts or time.time()) - LOG_RETENTION_DAYS * 86400
    removed = 0
    for path in glob.glob(os.path.join(RUN_LOG_DIR, "run_*.log")) + glob.glob(os.path.join(LOG_DIR, "worker_*.log")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass  # file sedang dipakai (log worker yang masih berjalan) -- coba lagi besok
    return removed


def run_daily(conn: sqlite3.Connection, now: datetime | None = None) -> dict | None:
    """Jalankan pemeliharaan kalau hari ini (WIB) belum. Return ringkasan, atau None kalau sudah."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(WIB).date().isoformat()
    if get_settings(conn, "maintenance.")["maintenance.last_date"] == today:
        return None
    set_settings(conn, {"maintenance.last_date": today}, "worker")  # tandai dulu: gagal pun tidak diulang tiap tick
    result = {"date": today, "backup": backup_db(conn, now), "logs_removed": prune_logs(now.timestamp())}
    result["sessions_removed"] = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),)).rowcount
    set_settings(conn, {"maintenance.last_result": result}, "worker")
    return result


def last_result(conn: sqlite3.Connection) -> dict | None:
    return get_settings(conn, "maintenance.").get("maintenance.last_result")
