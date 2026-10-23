"""
Setelan aplikasi NON-RAHASIA di tabel settings (nilai JSON), mis. daftar penerima notifikasi.
Kredensial (SMTP, SQL Server, AWS) TIDAK pernah disimpan di sini -- hanya di environment/.env.
"""

from __future__ import annotations

import json
import sqlite3

from pipeline.job_store import utc_now

DEFAULTS = {
    "notify.recipients": [],        # email yang selalu menerima notifikasi
    "notify.on_failed": True,       # email saat run gagal / terputus
    "notify.on_anomaly": True,      # email saat job 0 baris berturut-turut
    "notify.notify_creator": True,  # pembuat run ikut dikirimi (run manual)
    "notify.anomaly_streak": 3,     # berapa tanggal berturut-turut 0 baris sebelum dianggap anomali
    "maintenance.last_date": None,  # tanggal (WIB) pemeliharaan harian terakhir
}


def get_settings(conn: sqlite3.Connection, prefix: str = "") -> dict:
    stored = {r["key"]: json.loads(r["value"]) for r in conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?", (f"{prefix}%",))}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items() if k.startswith(prefix)} | \
        {k: v for k, v in stored.items() if k not in DEFAULTS}


def set_settings(conn: sqlite3.Connection, values: dict, by: str | None) -> None:
    now = utc_now()
    for k, v in values.items():
        conn.execute("INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
                     "updated_at = excluded.updated_at", (k, json.dumps(v), by, now))
