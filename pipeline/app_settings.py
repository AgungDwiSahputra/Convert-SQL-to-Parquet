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
    "notify.on_dq_warning": True,   # email ringkasan peringatan data quality per run (pipeline/dq.py)
    "dq.volume_low_pct": 50,        # jumlah baris < x% median riwayat -> peringatan volume
    "dq.volume_high_pct": 200,      # jumlah baris > x% median riwayat -> peringatan volume
    "dq.volume_min_history": 7,     # minimal riwayat (tanggal) sebelum volume harian/snapshot dinilai
    "dq.gap_days": 90,              # jangkauan cek celah job harian (hari)
    "dq.gap_weeks": 26,             # jangkauan cek celah job mingguan (minggu)
    "dq.gap_months": 12,            # jangkauan cek celah job bulanan (bulan)
    "freshness.enabled": False,     # penjaga kesegaran harian (pipeline/freshness.py) -- default nonaktif
    "freshness.check_time": "06:00",  # jam (WIB) pemeriksaan + email job terlambat
    "freshness.last_date": None,    # tanggal (WIB) penjaga terakhir berjalan
    "freshness.last_result": None,  # ringkasan pemeriksaan terakhir
    "s3inv.enabled": True,          # inventaris S3 harian (pipeline/s3_inventory.py) -- hanya membaca S3
    "s3inv.scan_time": "05:30",     # jam (WIB) pindai harian -- sebelum penjaga kesegaran
    "s3inv.last_date": None,        # tanggal (WIB) pindai harian terakhir
    "s3inv.last_result": None,      # ringkasan pindai terakhir (harian / manual)
    "notify.on_kb": True,           # email saat ingestion Knowledge Base gagal / ada dokumen gagal diindeks (pipeline/kb_sync.py)
    "kb.enabled": True,             # pantau ingestion Knowledge Base (worker) -- hanya membaca AWS
    "kb.poll_idle_seconds": 300,    # jeda sinkron riwayat ingestion saat tidak ada yang berjalan
    "kb.poll_active_seconds": 45,   # jeda saat ada ingestion yang masih berjalan
    "kb.glue_run_time": "03:00",    # jam (WIB) jadwal cleansing Glue dokumen SAAT INI (kelak run Glue dijalankan dari aplikasi ini)
    "kb.glue_deadline": "06:00",    # jam (WIB) batas: belum ada laporan run sesudah jadwal -> "Glue terlambat" + email
    "kb.pending_minutes": 120,      # penanda perubahan KB tanpa ingestion sesudahnya lebih lama dari ini -> "ingestion tertunda"
    "kb.raw_scan_minutes": 60,      # jeda pindai documents/raw/ (hanya informasi)
    "kb.raw_status": None,          # hasil pindai raw/ terakhir {checked_at, latest_at, objects, partial, error}
    "kb.active": False,             # ada ingestion berjalan pada sinkron terakhir (menentukan jeda di atas)
    "kb.last_poll_at": None,        # waktu (UTC) siklus sinkron terakhir dimulai
    "kb.last_result": None,         # ringkasan siklus sinkron terakhir
    "aws.last_check": None,         # hasil "Uji koneksi AWS" terakhir (Pengaturan)
    "auth.auto_viewer": True,       # akun SSO yang belum terdaftar otomatis masuk sebagai Viewer (Ekspor Cepat menunggu admin)
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
