"""
ANTRIAN & RIWAYAT RUN Web UI -- tabel runs / run_items / workers di jobs.db (skema di job_store.py).

Alur satu run:
  web app  : plan_items() -> create_run()             run 'queued',  item 'pending'
  worker   : claim_next_run()                          run 'running'
             start_item() / finish_item() per item     item 'running' -> 'success'/'failed'
             finish_run()                              run 'success'/'failed'/'cancelled'/'interrupted'

Semua fungsi di sini memakai koneksi dari open_db() (mode autocommit + transaksi eksplisit
BEGIN IMMEDIATE), karena web server dan worker adalah proses berbeda yang menulis ke file
SQLite yang sama.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from config import RUN_LOG_DIR
from job_store import JobRow, build_jobs, connect, utc_now, write_tx

# Run 'running' yang heartbeat-nya lebih tua dari ini dianggap worker-nya mati di tengah jalan.
HEARTBEAT_STALE_S = 90
# Worker dianggap online kalau pernah melapor dalam rentang ini.
WORKER_ONLINE_S = 45

_OPEN_ITEM = ("pending", "running")


def open_db() -> sqlite3.Connection:
    """
    Koneksi autocommit (isolation_level=None): transaksi multi-statement dibuka eksplisit lewat _tx().
    check_same_thread=False: FastAPI bisa membuka koneksi (dependency) di satu thread threadpool lalu
    memakai/menutupnya di thread lain. Aman karena satu koneksi hanya dipakai satu request, BERURUTAN
    (tidak pernah dibagi bersamaan antar-thread).
    """
    conn = connect(check_same_thread=False)
    conn.isolation_level = None
    return conn


# BEGIN IMMEDIATE: kunci tulis diambil di AWAL transaksi -- dua proses tidak bisa mengklaim run yang sama.
_tx = write_tx


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def run_log_path(run_id: int) -> str:
    return os.path.join(RUN_LOG_DIR, f"run_{run_id}.log")


# ---------------------------------------------------------------------------
# PERENCANAAN RUN
# ---------------------------------------------------------------------------

@dataclass
class PlannedItem:
    job_name: str
    target_date: str
    date_suffix: str | None
    template: str
    args: dict
    output: str
    s3_key: str | None      # path S3 tujuan; None = job tanpa tujuan S3, atau ditentukan saat run
    s3_dynamic: bool        # True = path S3 baru diketahui dari data (layout mingguan)


def plan_items(rows: list[JobRow], start: date, end: date) -> list[PlannedItem]:
    """
    Rincian eksekusi run -- semantik mode range SAMA dengan exporter.py: job harian sekali per
    tanggal (file lokal diberi sufiks tanggal), job master (needs_date=False) SEKALI saja untuk
    seluruh range dengan snapshot_date = tanggal awal.
    Beda kecil dengan CLI: di CLI job master yang GAGAL dicoba lagi di tanggal berikutnya;
    di sini tidak -- pakai "Ulangi yang belum berhasil" (retry_plan).
    """
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    is_range = len(dates) > 1
    by_name = {r.name: r for r in rows}
    done_master: set[str] = set()
    items = []
    for d in dates:
        for job in build_jobs(d.isoformat(), rows):
            if not job.needs_date:
                if job.name in done_master:
                    continue
                done_master.add(job.name)
            s3_key, dynamic = None, False
            if job.s3_key:
                try:
                    s3_key = job.s3_key({})
                except KeyError:  # s3_weekly butuh kolom minggu dari baris pertama hasil query
                    dynamic = True
            row = by_name[job.name]
            items.append(PlannedItem(job.name, d.isoformat(),
                                     d.strftime("%Y%m%d") if is_range and job.needs_date else None,
                                     row.template, row.args, job.output, s3_key, dynamic))
    return items


def retry_plan(conn: sqlite3.Connection, run_id: int, rows: list[JobRow]) -> list[PlannedItem]:
    """
    Item yang belum berhasil dari run `run_id`, dibangun ulang dengan definisi job SAAT INI
    (kalau definisinya sudah diperbaiki, retry memakai yang baru). Job yang sudah dihapus/
    dinonaktifkan -> ValueError.
    """
    by_name = {r.name: r for r in rows}
    todo = conn.execute("SELECT job_name, target_date, date_suffix FROM run_items "
                        "WHERE run_id = ? AND status != 'success' ORDER BY seq", (run_id,)).fetchall()
    missing = sorted({t["job_name"] for t in todo if t["job_name"] not in by_name})
    if missing:
        raise ValueError(f"job sudah tidak ada/nonaktif: {', '.join(missing)}")
    items = []
    for t in todo:
        row = by_name[t["job_name"]]
        job = build_jobs(t["target_date"], [row])[0]
        s3_key, dynamic = None, False
        if job.s3_key:
            try:
                s3_key = job.s3_key({})
            except KeyError:
                dynamic = True
        items.append(PlannedItem(job.name, t["target_date"], t["date_suffix"], row.template, row.args,
                                 job.output, s3_key, dynamic))
    return items


# ---------------------------------------------------------------------------
# WEB APP: buat & baca run
# ---------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, created_by: str, start: date, end: date, options: dict,
               items: list[PlannedItem], retry_of: int | None = None, schedule_id: int | None = None) -> int:
    with _tx(conn):
        cur = conn.execute(
            "INSERT INTO runs (status, created_by, created_at, start_date, end_date, options, retry_of, schedule_id) "
            "VALUES ('queued', ?, ?, ?, ?, ?, ?, ?)",
            (created_by, utc_now(), start.isoformat(), end.isoformat(), json.dumps(options), retry_of, schedule_id))
        run_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO run_items (run_id, seq, job_name, target_date, date_suffix, template, args, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            [(run_id, i + 1, it.job_name, it.target_date, it.date_suffix, it.template,
              json.dumps(it.args, ensure_ascii=False)) for i, it in enumerate(items)])
    return run_id


_RUN_SUMMARY_SQL = """
SELECT r.*,
       COUNT(i.id)                                       AS total_items,
       COALESCE(SUM(i.status = 'success'), 0)            AS ok_items,
       COALESCE(SUM(i.status = 'failed'), 0)             AS failed_items,
       COALESCE(SUM(i.status NOT IN ('pending', 'running')), 0) AS done_items,
       COUNT(DISTINCT i.job_name)                        AS job_count,
       COALESCE(SUM(i.rows), 0)                          AS total_rows
FROM runs r LEFT JOIN run_items i ON i.run_id = r.id
"""


def _run_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["options"] = json.loads(d["options"])
    d["cancel_requested"] = bool(d["cancel_requested"])
    return d


def list_runs(conn: sqlite3.Connection, limit: int = 50, before_id: int | None = None) -> list[dict]:
    rows = conn.execute(_RUN_SUMMARY_SQL + " WHERE (? IS NULL OR r.id < ?) GROUP BY r.id ORDER BY r.id DESC LIMIT ?",
                        (before_id, before_id, limit)).fetchall()
    return [_run_dict(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute(_RUN_SUMMARY_SQL + " WHERE r.id = ? GROUP BY r.id", (run_id,)).fetchone()
    if row is None or row["id"] is None:
        return None
    run = _run_dict(row)
    run["items"] = [dict(i, args=json.loads(i["args"])) for i in conn.execute(
        "SELECT * FROM run_items WHERE run_id = ? ORDER BY seq", (run_id,))]
    return run


def request_cancel(conn: sqlite3.Connection, run_id: int) -> str:
    """Run 'queued' langsung dibatalkan; run 'running' diberi tanda -- worker berhenti setelah item yang sedang jalan."""
    with _tx(conn):
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise LookupError(run_id)
        if row["status"] == "queued":
            now = utc_now()
            conn.execute("UPDATE runs SET status = 'cancelled', cancel_requested = 1, finished_at = ? WHERE id = ?",
                         (now, run_id))
            conn.execute("UPDATE run_items SET status = 'cancelled', finished_at = ? WHERE run_id = ?", (now, run_id))
            return "cancelled"
        if row["status"] == "running":
            conn.execute("UPDATE runs SET cancel_requested = 1 WHERE id = ?", (run_id,))
            return "cancel_requested"
        return row["status"]


def read_run_log(run_id: int, offset: int = 0, max_bytes: int = 256 * 1024) -> tuple[str, int]:
    """Potongan log run mulai byte `offset` -> (teks, offset berikutnya). Dipakai UI untuk log live (polling)."""
    path = run_log_path(run_id)
    if not os.path.exists(path):
        return "", 0
    with open(path, "rb") as fh:
        fh.seek(max(offset, 0))
        chunk = fh.read(max_bytes)
    return chunk.decode("utf-8", errors="replace"), max(offset, 0) + len(chunk)


def worker_online(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM workers WHERE last_seen_at >= ? LIMIT 1",
                        (_ago(WORKER_ONLINE_S),)).fetchone() is not None


# ---------------------------------------------------------------------------
# WORKER: klaim & eksekusi
# ---------------------------------------------------------------------------

def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def worker_seen(conn: sqlite3.Connection, wid: str, current_run_id: int | None = None) -> None:
    now = utc_now()
    conn.execute("INSERT INTO workers (id, started_at, last_seen_at, current_run_id) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at, "
                 "current_run_id = excluded.current_run_id", (wid, now, now, current_run_id))


def claim_next_run(conn: sqlite3.Connection) -> int | None:
    """
    Klaim run 'queued' tertua yang job-nya TIDAK sedang dipakai run lain yang 'running' --
    dua run dengan job yang sama berjalan bersamaan akan saling menimpa file lokal/partisi S3.
    """
    with _tx(conn):
        busy = {r[0] for r in conn.execute(
            "SELECT DISTINCT i.job_name FROM run_items i JOIN runs r ON r.id = i.run_id WHERE r.status = 'running'")}
        for (run_id,) in conn.execute("SELECT id FROM runs WHERE status = 'queued' ORDER BY id").fetchall():
            names = {r[0] for r in conn.execute("SELECT DISTINCT job_name FROM run_items WHERE run_id = ?", (run_id,))}
            if names & busy:
                continue
            now = utc_now()
            conn.execute("UPDATE runs SET status = 'running', started_at = ?, heartbeat_at = ? WHERE id = ?",
                         (now, now, run_id))
            return run_id
    return None


def heartbeat(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?", (utc_now(), run_id))


def mark_stale_runs(conn: sqlite3.Connection) -> list[int]:
    """Run 'running' yang heartbeat-nya basi (worker mati/di-restart di tengah run) -> 'interrupted'."""
    with _tx(conn):
        stale = [r[0] for r in conn.execute("SELECT id FROM runs WHERE status = 'running' AND heartbeat_at < ?",
                                            (_ago(HEARTBEAT_STALE_S),))]
        now = utc_now()
        for run_id in stale:
            conn.execute("UPDATE run_items SET status = 'interrupted', finished_at = ? "
                         "WHERE run_id = ? AND status IN ('pending', 'running')", (now, run_id))
            conn.execute("UPDATE runs SET status = 'interrupted', finished_at = ?, "
                         "error = 'Worker berhenti di tengah run (tidak ada heartbeat).' WHERE id = ?", (now, run_id))
    return stale


def is_cancel_requested(conn: sqlite3.Connection, run_id: int) -> bool:
    return bool(conn.execute("SELECT cancel_requested FROM runs WHERE id = ?", (run_id,)).fetchone()[0])


def start_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("UPDATE run_items SET status = 'running', started_at = ? WHERE id = ?", (utc_now(), item_id))


def finish_item(conn: sqlite3.Connection, item_id: int, status: str, result=None, error: str | None = None) -> None:
    """result = exporter.JobResult (untuk status 'success')."""
    conn.execute(
        "UPDATE run_items SET status = ?, rows = ?, size_bytes = ?, location = ?, s3_key = ?, elapsed_s = ?, "
        "error = ?, finished_at = ? WHERE id = ?",
        (status, getattr(result, "rows", None), getattr(result, "size_bytes", None),
         getattr(result, "location", None), getattr(result, "s3_key", None),
         getattr(result, "elapsed_s", None), error, utc_now(), item_id))


def close_open_items(conn: sqlite3.Connection, run_id: int, status: str) -> int:
    """Tutup semua item yang belum selesai (pending/running) dengan `status`. Return jumlah item."""
    cur = conn.execute("UPDATE run_items SET status = ?, finished_at = ? WHERE run_id = ? "
                       "AND status IN ('pending', 'running')", (status, utc_now(), run_id))
    return cur.rowcount


def finish_run(conn: sqlite3.Connection, run_id: int, error: str | None = None) -> str:
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM run_items WHERE run_id = ? GROUP BY status",
                               (run_id,)).fetchall())
    if counts.get("interrupted"):
        status = "interrupted"
    elif is_cancel_requested(conn, run_id) and counts.get("cancelled"):
        status = "cancelled"
    elif error or counts.get("failed"):
        status = "failed"
    else:
        status = "success"
    conn.execute("UPDATE runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                 (status, utc_now(), error, run_id))
    return status
