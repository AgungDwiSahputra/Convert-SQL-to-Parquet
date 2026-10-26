"""
DATABASE APLIKASI -- SQLite (file JOBS_DB di config.py, default jobs.db).

Tabel:
  jobs       : daftar ekspor. Satu baris = nama + template (lihat job_templates.py) +
               parameter template dalam JSON. exporter.py & worker.py membaca dari sini.
  users      : user Web UI yang boleh login (via SSO) + role-nya.        (skema v2)
  sessions   : session login Web UI.                                     (skema v2)
  runs       : run yang diantrikan dari Web UI (lihat run_store.py).     (skema v2)
  run_items  : rincian run: satu baris = satu job x satu tanggal.        (skema v2)

File database sengaja masuk .gitignore -- isinya query/nama tabel/S3 key nyata milik
environment ini (alasan yang sama dengan jobs.py).

Mengisi daftar job pertama kali: python migrate_jobs.py (impor dari jobs.py).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pipeline.config import JOBS_DB
from pipeline.job import Job
from pipeline.job_templates import build_job, s3_folder

# Migrasi skema berurutan: elemen ke-N membawa database ke PRAGMA user_version = N+1.
# Struktur berubah = TAMBAH elemen baru di akhir, jangan ubah elemen lama (database
# yang sudah ada di server hanya menjalankan elemen yang belum pernah dijalankan).
_MIGRATIONS = [
    # v1 -- Fase 0: daftar job
    """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    template    TEXT    NOT NULL,              -- kunci TEMPLATES di job_templates.py
    args        TEXT    NOT NULL,              -- JSON: argumen template (tanpa name & target_date)
    enabled     INTEGER NOT NULL DEFAULT 1,    -- 0 = nonaktif (dulu: entri di-comment di jobs.py)
    sort_order  INTEGER NOT NULL,              -- urutan eksekusi dalam satu tanggal
    notes       TEXT,                          -- catatan/alasan (dulu: komentar di atas entri jobs.py)
    created_at  TEXT    NOT NULL,              -- ISO-8601 UTC
    updated_at  TEXT    NOT NULL
);
""",
    # v2 -- Fase 1: Web UI (login SSO, antrian & riwayat run)
    """
CREATE TABLE IF NOT EXISTS users (
    username       TEXT    PRIMARY KEY,         -- user_name dari SSO, huruf kecil
    full_name      TEXT,
    email          TEXT,
    role           TEXT    NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL,
    created_by     TEXT,
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,               -- sha256 token cookie; token asli TIDAK disimpan
    username    TEXT NOT NULL REFERENCES users(username),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY,
    status            TEXT    NOT NULL,         -- queued|running|success|failed|cancelled|interrupted
    created_by        TEXT    NOT NULL,
    created_at        TEXT    NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    heartbeat_at      TEXT,                     -- diperbarui worker tiap beberapa detik selama running
    start_date        TEXT    NOT NULL,
    end_date          TEXT    NOT NULL,
    options           TEXT    NOT NULL,         -- JSON: batch, compression, skip_s3, skip_local, stop_on_error
    retry_of          INTEGER REFERENCES runs(id),
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    error             TEXT                      -- error level run (mis. koneksi SQL Server gagal)
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS run_items (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    seq          INTEGER NOT NULL,              -- urutan eksekusi dalam run
    job_name     TEXT    NOT NULL,
    target_date  TEXT    NOT NULL,
    date_suffix  TEXT,                          -- sufiks file lokal di mode range (lihat export.resolve_output)
    template     TEXT    NOT NULL,              -- SNAPSHOT definisi job saat run dibuat (audit:
    args         TEXT    NOT NULL,              --   run ini dijalankan dengan definisi apa)
    status       TEXT    NOT NULL,              -- pending|running|success|failed|skipped|cancelled|interrupted
    rows         INTEGER,
    size_bytes   INTEGER,
    location     TEXT,
    s3_key       TEXT,
    elapsed_s    REAL,
    error        TEXT,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_items_run ON run_items(run_id, seq);

CREATE TABLE IF NOT EXISTS workers (
    id              TEXT PRIMARY KEY,           -- hostname:pid proses worker.py
    started_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,              -- UI menganggap worker online kalau baru-baru ini melapor
    current_run_id  INTEGER
);
""",
    # v3 -- Fase 2: tambah/ubah job lewat Web UI
    """
-- Path S3 job ini sudah (mungkin) dipakai produksi -> layer/module/dataset/nama file hanya boleh
-- diubah admin dengan konfirmasi (path lama tertinggal di S3). NULL = belum pernah upload.
ALTER TABLE jobs ADD COLUMN s3_locked_at TEXT;
ALTER TABLE jobs ADD COLUMN updated_by TEXT;

CREATE TABLE IF NOT EXISTS job_history (
    id          INTEGER PRIMARY KEY,
    job_name    TEXT NOT NULL,
    action      TEXT NOT NULL,                  -- import|create|update|enable|disable|s3_move
    changed_by  TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    before      TEXT,                           -- JSON {template, args, enabled, notes}
    after       TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_history_job ON job_history(job_name, id);

-- Job hasil impor jobs.py yang punya tujuan S3: anggap path-nya sudah dipakai (scheduler CLI
-- sudah berjalan berbulan-bulan sebelum Web UI ada).
UPDATE jobs SET s3_locked_at = created_at
 WHERE template LIKE 'bronze%' OR json_extract(args, '$.s3') IS NOT NULL;
UPDATE jobs SET updated_by = 'migrate_jobs.py';
INSERT INTO job_history (job_name, action, changed_by, changed_at, after)
SELECT name, 'import', 'migrate_jobs.py', created_at,
       json_object('template', template, 'args', json(args), 'enabled', enabled, 'notes', notes)
  FROM jobs;
""",
    # v4 -- Fase 3: jadwal, notifikasi email, ekspor ad-hoc, pemeliharaan
    """
CREATE TABLE IF NOT EXISTS schedules (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,
    enabled       INTEGER NOT NULL DEFAULT 0,      -- default nonaktif (hindari dobel dgn Task Scheduler)
    frequency     TEXT    NOT NULL CHECK (frequency IN ('daily', 'weekly')),
    time_of_day   TEXT    NOT NULL,                -- 'HH:MM' jam dinding WIB
    weekdays      TEXT,                            -- JSON [0..6], Senin = 0 (khusus weekly)
    date_offset   INTEGER NOT NULL DEFAULT 1,      -- tanggal data = tanggal jadwal (WIB) - offset hari
    selector      TEXT    NOT NULL,                -- JSON {"mode": "pattern"|"modules"|"jobs", ...}
    options       TEXT    NOT NULL,                -- JSON opsi run (sama dengan run manual)
    next_run_at   TEXT,                            -- ISO UTC; NULL kalau nonaktif
    last_run_at   TEXT,
    last_run_id   INTEGER,
    last_message  TEXT,
    created_by    TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_by    TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at);
ALTER TABLE runs ADD COLUMN schedule_id INTEGER REFERENCES schedules(id);

-- Setelan aplikasi yang BUKAN rahasia (mis. daftar penerima notifikasi). Kredensial SMTP TIDAK di
-- sini -- hanya di environment/.env server (config.py).
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,                     -- JSON
    updated_by  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,                     -- run_failed | anomaly | test
    subject     TEXT NOT NULL,
    recipients  TEXT NOT NULL,                     -- JSON list
    status      TEXT NOT NULL,                     -- sent | failed | skipped
    error       TEXT,
    run_id      INTEGER,
    job_names   TEXT,                              -- JSON list (anomaly)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_kind ON notifications(kind, id);

CREATE TABLE IF NOT EXISTS adhoc_exports (
    id           INTEGER PRIMARY KEY,
    username     TEXT NOT NULL,
    mode         TEXT NOT NULL,                    -- job | table
    source       TEXT NOT NULL,                    -- nama job, atau db.dbo.tabel
    target_date  TEXT,
    rows         INTEGER,
    truncated    INTEGER NOT NULL DEFAULT 0,
    size_bytes   INTEGER,
    status       TEXT NOT NULL,                    -- success | failed
    error        TEXT,
    created_at   TEXT NOT NULL
);
""",
    # v5 -- balasan mail server + Message-ID per notifikasi (bahan 'message trace' kalau email tidak sampai)
    """
ALTER TABLE notifications ADD COLUMN detail TEXT;
""",
    # v6 -- data quality Bronze (pipeline/dq.py)
    """
ALTER TABLE jobs ADD COLUMN schema_json TEXT;          -- skema terakhir yang DIPUBLIKASIKAN (acuan DQ)
ALTER TABLE jobs ADD COLUMN schema_updated_at TEXT;
ALTER TABLE run_items ADD COLUMN source_rows INTEGER;  -- COUNT sumber dengan filter yang sama
ALTER TABLE run_items ADD COLUMN dq_status TEXT;       -- ok|warning|blocked|unchecked (NULL = sebelum v6)
ALTER TABLE run_items ADD COLUMN dq_detail TEXT;       -- JSON daftar pemeriksaan
""",
]
SCHEMA_VERSION = len(_MIGRATIONS)

# WIB dikunci eksplisit (BUKAN timezone OS server) -- konsisten dengan asumsi WIB pada
# kolom datetime SQL Server. Tanpa ini, server ber-timezone UTC menghitung "kemarin"
# salah 1 hari untuk run antara 00:00-07:00 WIB.
_TZ_WIB = ZoneInfo("Asia/Jakarta")


class JobStoreError(Exception):
    """Database job belum ada atau tidak bisa dibaca."""


@dataclass
class JobRow:
    name: str
    template: str
    args: dict
    enabled: bool
    sort_order: int
    notes: str | None = None
    s3_locked_at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None

    def snapshot(self) -> dict:
        """Isi job yang dicatat di job_history (sebelum/sesudah perubahan)."""
        return {"template": self.template, "args": self.args, "enabled": self.enabled, "notes": self.notes}


def default_target_date() -> str:
    """Tanggal proses default = KEMARIN menurut kalender WIB, format YYYY-MM-DD."""
    return (datetime.now(_TZ_WIB) - timedelta(days=1)).strftime("%Y-%m-%d")


def utc_now() -> str:
    """Timestamp ISO-8601 UTC -- format seragam supaya bisa dibandingkan sebagai string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str = JOBS_DB, create: bool = False, check_same_thread: bool = True) -> sqlite3.Connection:
    """
    Buka database. create=False -> error jelas kalau file belum ada, BUKAN diam-diam
    membuat database kosong (sqlite3.connect biasa membuatnya, lalu exporter jalan
    dengan 0 job tanpa error apa pun).
    Mode WAL: web server dan worker (proses berbeda) bisa membaca selagi yang lain menulis;
    timeout=10 -> menunggu lock sampai 10 detik, bukan langsung error "database is locked".
    """
    if not create and not os.path.exists(path):
        raise JobStoreError(f"Database job belum ada: {path}\n"
                            f"  Jalankan dulu: python migrate_jobs.py")
    conn = sqlite3.connect(path, timeout=10, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Jalankan migrasi skema yang belum pernah dijalankan di database ini."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in enumerate(_MIGRATIONS[current:], start=current + 1):
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


@contextmanager
def write_tx(conn: sqlite3.Connection):
    """
    Transaksi tulis eksplisit untuk koneksi autocommit (isolation_level=None, lihat
    run_store.open_db). BEGIN IMMEDIATE = kunci tulis diambil di AWAL, jadi dua proses (web &
    worker) tidak saling menimpa di tengah transaksi.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _row(r: sqlite3.Row) -> JobRow:
    keys = r.keys()
    return JobRow(r["name"], r["template"], json.loads(r["args"]), bool(r["enabled"]), r["sort_order"],
                  r["notes"], *(r[k] if k in keys else None for k in ("s3_locked_at", "updated_by", "updated_at")))


def load_job_rows(enabled_only: bool = True, path: str = JOBS_DB) -> list[JobRow]:
    """Baca daftar job, urut sesuai urutan eksekusi."""
    conn = connect(path)
    try:
        sql = "SELECT * FROM jobs"
        if enabled_only:
            sql += " WHERE enabled = 1"
        rows = conn.execute(sql + " ORDER BY sort_order, id").fetchall()
    except sqlite3.Error as e:
        raise JobStoreError(f"Gagal membaca database job {path}: {e}") from e
    finally:
        conn.close()
    return [_row(r) for r in rows]


def build_jobs(target_date: str, rows: list[JobRow] | None = None) -> list[Job]:
    """
    Bangun objek Job untuk SATU tanggal proses (pengganti build_jobs() di jobs.py lama).
    rows=None -> baca job aktif dari database. exporter.py mengirim rows yang dibaca SEKALI
    di awal run, supaya perubahan database di tengah mode range tidak membuat tanggal
    awal dan akhir memakai definisi job yang berbeda.
    """
    if rows is None:
        rows = load_job_rows()
    return [build_job(r.name, r.template, r.args, target_date) for r in rows]


def replace_all_jobs(conn: sqlite3.Connection, rows: list[JobRow], by: str = "migrate_jobs.py") -> None:
    """
    Hapus semua job lalu isi ulang dengan `rows`, dalam SATU transaksi (dipakai migrate_jobs.py).
    Job bertujuan S3 langsung dikunci (lihat kolom s3_locked_at); riwayat lama TIDAK dihapus,
    impor ini ditambahkan sebagai entri 'import'.
    """
    now = utc_now()
    with conn:
        conn.execute("DELETE FROM jobs")
        conn.executemany(
            "INSERT INTO jobs (name, template, args, enabled, sort_order, notes, created_at, updated_at, "
            "s3_locked_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r.name, r.template, _dumps(r.args), int(r.enabled), r.sort_order, r.notes, now, now,
              now if s3_folder(r.template, r.args) else None, by) for r in rows],
        )
        conn.executemany(
            "INSERT INTO job_history (job_name, action, changed_by, changed_at, after) VALUES (?, 'import', ?, ?, ?)",
            [(r.name, by, now, _dumps(r.snapshot())) for r in rows],
        )


# ---------------------------------------------------------------------------
# TAMBAH / UBAH JOB (Web UI) -- semua perubahan tercatat di job_history
# ---------------------------------------------------------------------------

def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _history(conn: sqlite3.Connection, name: str, action: str, by: str, before: dict | None, after: dict | None):
    conn.execute("INSERT INTO job_history (job_name, action, changed_by, changed_at, before, after) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (name, action, by, utc_now(), _dumps(before) if before else None, _dumps(after) if after else None))


def get_job_row(conn: sqlite3.Connection, name: str) -> JobRow | None:
    r = conn.execute("SELECT * FROM jobs WHERE name = ?", (name,)).fetchone()
    return _row(r) if r else None


def job_history(conn: sqlite3.Connection, name: str, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM job_history WHERE job_name = ? ORDER BY id DESC LIMIT ?", (name, limit))
    return [dict(r, before=json.loads(r["before"]) if r["before"] else None,
                 after=json.loads(r["after"]) if r["after"] else None) for r in rows]


def create_job(conn: sqlite3.Connection, row: JobRow, by: str) -> None:
    """Job baru di urutan PALING AKHIR. sqlite3.IntegrityError kalau nama sudah dipakai."""
    now = utc_now()
    with write_tx(conn):
        order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM jobs").fetchone()[0]
        conn.execute("INSERT INTO jobs (name, template, args, enabled, sort_order, notes, created_at, updated_at, "
                     "updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (row.name, row.template, _dumps(row.args), int(row.enabled), order, row.notes, now, now, by))
        _history(conn, row.name, "create", by, None, row.snapshot())


def update_job(conn: sqlite3.Connection, old: JobRow, new: JobRow, by: str, s3_moved: bool = False) -> None:
    """
    Simpan perubahan template/args/notes. s3_moved=True (admin sudah konfirmasi pindah path S3)
    -> kunci S3 dilepas: path BARU belum pernah dipakai, terkunci lagi saat upload pertama.
    Template/args berubah -> skema acuan DQ dilepas (perubahan disengaja; run berikutnya jadi acuan).
    """
    definition_changed = new.template != old.template or new.args != old.args
    with write_tx(conn):
        conn.execute("UPDATE jobs SET template = ?, args = ?, notes = ?, updated_at = ?, updated_by = ?, "
                     "s3_locked_at = CASE WHEN ? THEN NULL ELSE s3_locked_at END, "
                     "schema_json = CASE WHEN ? THEN NULL ELSE schema_json END WHERE name = ?",
                     (new.template, _dumps(new.args), new.notes, utc_now(), by, int(s3_moved),
                      int(definition_changed), old.name))
        _history(conn, old.name, "s3_move" if s3_moved else "update", by, old.snapshot(),
                 {**new.snapshot(), "enabled": old.enabled})


def set_job_enabled(conn: sqlite3.Connection, old: JobRow, enabled: bool, by: str) -> None:
    with write_tx(conn):
        conn.execute("UPDATE jobs SET enabled = ?, updated_at = ?, updated_by = ? WHERE name = ?",
                     (int(enabled), utc_now(), by, old.name))
        _history(conn, old.name, "enable" if enabled else "disable", by, old.snapshot(),
                 {**old.snapshot(), "enabled": enabled})


def mark_s3_uploaded(name: str, conn: sqlite3.Connection | None = None) -> bool:
    """
    Kunci path S3 job saat upload PERTAMA berhasil (dipanggil exporter.py CLI & worker.py).
    Gagal mencatat (mis. database terkunci lama) TIDAK boleh menggagalkan job yang uploadnya
    sudah sukses -> error ditelan, return False.
    """
    own = conn is None
    try:
        conn = conn or connect()
        conn.execute("UPDATE jobs SET s3_locked_at = ? WHERE name = ? AND s3_locked_at IS NULL", (utc_now(), name))
        if own:
            conn.commit()
        return True
    except (sqlite3.Error, JobStoreError):
        return False
    finally:
        if own and conn is not None:
            conn.close()


def get_schema_baseline(name: str, conn: sqlite3.Connection | None = None) -> list | None:
    """Skema acuan DQ job (jobs.schema_json). None = belum ada / tidak terbaca (run berikutnya jadi acuan)."""
    own = conn is None
    try:
        conn = conn or connect()
        r = conn.execute("SELECT schema_json FROM jobs WHERE name = ?", (name,)).fetchone()
        return json.loads(r[0]) if r and r[0] else None
    except (sqlite3.Error, JobStoreError, ValueError):
        return None
    finally:
        if own and conn is not None:
            conn.close()


def save_schema_baseline(name: str, schema: list | None, conn: sqlite3.Connection | None = None) -> bool:
    """
    Simpan skema acuan DQ (schema=None -> lepas acuan, lihat `manage.py accept-schema`). Sama seperti
    mark_s3_uploaded: gagal mencatat tidak boleh menggagalkan job yang sudah sukses -> return False.
    """
    own = conn is None
    try:
        conn = conn or connect()
        cur = conn.execute("UPDATE jobs SET schema_json = ?, schema_updated_at = ? WHERE name = ?",
                           (_dumps(schema) if schema is not None else None, utc_now(), name))
        if own:
            conn.commit()
        return cur.rowcount > 0
    except (sqlite3.Error, JobStoreError):
        return False
    finally:
        if own and conn is not None:
            conn.close()


def ui_edit_summary(conn: sqlite3.Connection) -> dict | None:
    """Ringkasan perubahan job lewat Web UI (selain impor) -- None kalau belum ada."""
    r = conn.execute("SELECT COUNT(*) AS n, MAX(changed_at) AS last_at FROM job_history "
                     "WHERE action != 'import'").fetchone()
    if not r["n"]:
        return None
    last = conn.execute("SELECT job_name, changed_by FROM job_history WHERE action != 'import' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    return {"count": r["n"], "last_at": r["last_at"], "last_by": last["changed_by"], "last_job": last["job_name"]}
