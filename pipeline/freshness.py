"""
KESEGARAN DATA -- apakah tiap job aktif sudah memproses tanggal terbaru yang wajar untuk iramanya.

Tanggal dianggap SUDAH DIPROSES kalau di dq_results ada hasil yang tidak ditahan DQ dan (sudah
dipublikasikan ATAU memang 0 baris -- tidak ada yang perlu diupload). Run uji --skip-s3 yang berisi
data tidak dihitung. Run CLI ikut terhitung selama memakai jobs.db yang sama.

Tanggal yang diharapkan (hari ini WIB = T):
  harian / snapshot : T-1 (proses hari ini mengolah H-1)
  mingguan          : Minggu terakhir yang <= T-1 (minggu Senin-Minggu sudah ditutup & dijalankan Senin)
  bulanan           : akhir bulan terakhir yang <= T-3 (bulan lalu dijalankan sekitar tanggal 3)

Penjaga harian (run_daily, dipanggil thread scheduler di worker): sekali per hari setelah jam cek
(default 06:00 WIB), kirim email daftar job terlambat. Default NONAKTIF -- aktifkan di Pengaturan
setelah jadwal pindah dari Task Scheduler ke aplikasi (run Task Scheduler di mesin lain dengan
jobs.db lain tidak terlihat di sini).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from pipeline import notify
from pipeline.app_settings import get_settings, set_settings
from pipeline.job_store import JobRow, load_job_rows
from pipeline.scheduler import WIB


def expected_date(cadence: str, today: date) -> date:
    if cadence == "weekly":
        ref = today - timedelta(days=1)
        return ref - timedelta(days=(ref.weekday() + 1) % 7)
    if cadence == "monthly":
        ref = today - timedelta(days=3)
        return (ref + timedelta(days=1)).replace(day=1) - timedelta(days=1)
    return today - timedelta(days=1)


def latest_processed(conn: sqlite3.Connection) -> dict[str, str]:
    """job -> tanggal terbaru yang sudah diproses (lihat docstring modul)."""
    return dict(conn.execute(
        "SELECT job_name, MAX(target_date) FROM dq_results WHERE dq_status != 'blocked' "
        "AND (published = 1 OR rows = 0) GROUP BY job_name").fetchall())


def job_freshness(conn: sqlite3.Connection, rows: list[JobRow], today: date | None = None) -> dict[str, dict]:
    """job -> {status: fresh|late|unknown, cadence, latest, expected, lag_days}."""
    today = today or datetime.now(WIB).date()
    latest = latest_processed(conn)
    out = {}
    for r in rows:
        cadence = r.effective_cadence
        expected = expected_date(cadence, today)
        last = latest.get(r.name)
        if last is None:
            status, lag = "unknown", None
        else:
            lag = (expected - date.fromisoformat(last)).days
            status = "fresh" if lag <= 0 else "late"
        out[r.name] = {"status": status, "cadence": cadence, "latest": last, "expected": expected.isoformat(),
                       "lag_days": max(lag, 0) if lag is not None else None}
    return out


def run_daily(conn: sqlite3.Connection, now: datetime | None = None) -> dict | None:
    """Penjaga harian. Return ringkasan kalau berjalan putaran ini, None kalau belum waktunya / sudah / nonaktif."""
    s = get_settings(conn, "freshness.")
    if not s["freshness.enabled"]:
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(WIB)
    hh, mm = (int(x) for x in s["freshness.check_time"].split(":"))
    if now.time() < time(hh, mm) or s["freshness.last_date"] == now.date().isoformat():
        return None
    set_settings(conn, {"freshness.last_date": now.date().isoformat()}, "worker")  # tandai dulu: gagal pun tidak diulang
    rows = load_job_rows()   # job aktif saja
    fresh = job_freshness(conn, rows, now.date())
    late = [{"job_name": n, **f} for n, f in fresh.items() if f["status"] != "fresh"]
    result = {"date": now.date().isoformat(), "checked": len(rows), "late": late}
    set_settings(conn, {"freshness.last_result": result}, "worker")
    if late:
        notify.notify_stale(conn, result)
    return result
