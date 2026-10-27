#!/usr/bin/env python3
"""
WORKER -- mengeksekusi run yang diantrikan dari Web UI (tabel runs/run_items di jobs.db).

Proses terpisah dari web server karena ekspor bisa berjalan menit sampai jam -- tidak boleh
di dalam HTTP request. Satu worker mengerjakan run satu per satu, supaya beban ke SQL Server
produksi terkendali. Dua run yang memakai job yang sama tidak pernah berjalan bersamaan,
walaupun worker dijalankan lebih dari satu (lihat run_store.claim_next_run).

Selain itu worker menjalankan thread SCHEDULER (scheduler.py): jadwal yang jatuh tempo diubah
menjadi run di antrian, plus pemeliharaan harian (maintenance.py). Thread terpisah supaya jadwal
tetap tepat waktu walaupun loop utama sedang mengerjakan run yang panjang. Setelah run selesai,
notifikasi email dikirim (notify.py) -- gagal kirim tidak memengaruhi status run.

    python worker.py           # jalan terus, cek antrian tiap 3 detik (Ctrl+C untuk berhenti)
    python worker.py --once    # satu putaran scheduler + proses semua antrian yang ada, lalu keluar

Log tiap run: logs/runs/run_<id>.log (ditampilkan live di halaman detail run).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

import pyodbc

from pipeline import dq, freshness, maintenance, notify, run_store, scheduler
from pipeline.config import CONN_STR
from pipeline.export import LOG_DATEFMT, LOG_FORMAT, log, make_s3_client, run_job, setup_logging
from pipeline.job_store import JobStoreError, init_db, mark_s3_uploaded
from pipeline.job_templates import build_job

POLL_INTERVAL_S = 3
HEARTBEAT_INTERVAL_S = 15
SCHEDULER_INTERVAL_S = 20


def scheduler_round(db) -> None:
    """Satu putaran: tembak jadwal jatuh tempo + pemeliharaan harian. Error dicatat, tidak dilempar."""
    try:
        for run_id in scheduler.tick(db):
            log.info("Jadwal membuat run #%s.", run_id)
    except Exception:  # noqa: BLE001 -- scheduler tidak boleh mematikan worker
        log.exception("Scheduler gagal di putaran ini.")
    try:
        result = maintenance.run_daily(db)
        if result:
            log.info("Pemeliharaan harian: backup %s, %s log lama dihapus, %s session kedaluwarsa dihapus.",
                     result["backup"], result["logs_removed"], result["sessions_removed"])
    except Exception:  # noqa: BLE001
        log.exception("Pemeliharaan harian gagal.")
    try:
        result = freshness.run_daily(db)
        if result:
            log.log(logging.WARNING if result["late"] else logging.INFO,
                    "Pemeriksaan kesegaran: %s dari %s job aktif belum segar (terlambat / belum ada data).", len(result["late"]), result["checked"])
    except Exception:  # noqa: BLE001
        log.exception("Pemeriksaan kesegaran gagal.")


class _Scheduler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="scheduler")
        self.stopped = threading.Event()

    def run(self):
        db = run_store.open_db()
        try:
            while True:
                scheduler_round(db)
                if self.stopped.wait(SCHEDULER_INTERVAL_S):
                    break
        finally:
            db.close()


class _Heartbeat(threading.Thread):
    """Selama run berjalan: perbarui runs.heartbeat_at + workers.last_seen_at (koneksi SQLite sendiri)."""

    def __init__(self, wid: str, run_id: int):
        super().__init__(daemon=True)
        self.wid, self.run_id = wid, run_id
        self.stopped = threading.Event()

    def run(self):
        db = run_store.open_db()
        try:
            while not self.stopped.wait(HEARTBEAT_INTERVAL_S):
                run_store.heartbeat(db, self.run_id)
                run_store.worker_seen(db, self.wid, self.run_id)
        finally:
            db.close()


def execute_run(db, wid: str, run_id: int) -> str:
    run = run_store.get_run(db, run_id)
    opts = run["options"]
    os.makedirs(run_store.RUN_LOG_DIR, exist_ok=True)
    handler = logging.FileHandler(run_store.run_log_path(run_id), encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATEFMT))
    log.addHandler(handler)
    hb = _Heartbeat(wid, run_id)
    hb.start()
    run_store.worker_seen(db, wid, run_id)

    run_error = None
    executed = {}   # job -> (irama, {tanggal sukses}) -- bahan penilaian volume per periode
    try:
        log.info("Run #%s oleh %s: %d item, tanggal %s s/d %s, opsi %s", run_id, run["created_by"],
                 len(run["items"]), run["start_date"], run["end_date"], opts)
        s3_client = make_s3_client(opts["skip_s3"])
        comp = None if opts["compression"] == "none" else opts["compression"]
        log.info("Menghubungkan ke SQL Server...")
        with pyodbc.connect(CONN_STR) as conn:
            for item in run["items"]:
                if run_store.is_cancel_requested(db, run_id):
                    log.warning("Run #%s dibatalkan -- sisa item tidak dijalankan.", run_id)
                    run_store.close_open_items(db, run_id, "cancelled")
                    break
                run_store.start_item(db, item["id"])
                try:
                    job = build_job(item["job_name"], item["template"], item["args"], item["target_date"])
                    cadence = dq.cadence_of(db, item["job_name"], item["template"], item["args"])
                    audit = dq.prepare(db, item["job_name"], item["target_date"], cadence)
                    result = run_job(conn, job, opts["batch"], comp, s3_client, item["date_suffix"],
                                     opts["skip_local"], audit)
                except Exception as e:  # noqa: BLE001 -- satu job gagal tidak menghentikan run
                    log.error("[%s] (%s) GAGAL: %s", item["job_name"], item["target_date"], e)
                    if isinstance(e, dq.DQBlocked):
                        dq.after_job(db, job, audit, error=e, run_id=run_id)
                    run_store.finish_item(db, item["id"], "failed", error=str(e),
                                          dq_report=getattr(e, "report", None))  # dq.DQBlocked membawa laporan
                    if opts["stop_on_error"]:
                        log.error("Berhenti karena stop_on_error -- sisa item dilewati.")
                        run_store.close_open_items(db, run_id, "skipped")
                        break
                else:
                    run_store.finish_item(db, item["id"], "success", result)
                    dq.after_job(db, job, audit, result, run_id=run_id)
                    if result.s3_key:
                        mark_s3_uploaded(item["job_name"], db)  # path S3 sudah dipakai -> dikunci
                    executed.setdefault(item["job_name"], (cadence, set()))[1].add(item["target_date"])
            for f in dq.run_period_findings(db, executed):
                log.warning("[%s] DQ volume periode %s: %s", f["job_name"], f["period"], f["message"])
    except pyodbc.Error as e:
        run_error = f"Kesalahan koneksi SQL Server: {e}"
        log.error(run_error)
    except Exception as e:  # noqa: BLE001 -- jangan biarkan run tergantung di status 'running'
        run_error = f"Error worker: {e}"
        log.exception(run_error)
    finally:
        # Item yang masih terbuka di sini = run terputus (error koneksi / Ctrl+C di tengah job).
        run_store.close_open_items(db, run_id, "failed" if run_error else "interrupted")
        status = run_store.finish_run(db, run_id, run_error)
        hb.stopped.set()
        run_store.worker_seen(db, wid)
        log.info("Run #%s selesai: %s", run_id, status.upper())
        log.removeHandler(handler)
        handler.close()
    try:
        notify.notify_run(db, run_id)
    except Exception:  # noqa: BLE001 -- notifikasi gagal tidak boleh memengaruhi run/worker
        log.exception("Notifikasi run #%s gagal diproses.", run_id)
    return status


def main() -> int:
    p = argparse.ArgumentParser(description="Worker eksekusi run dari Web UI.")
    p.add_argument("--once", action="store_true", help="proses antrian yang ada lalu keluar")
    args = p.parse_args()

    setup_logging("worker")
    try:
        db = run_store.open_db()
        init_db(db)
    except JobStoreError as e:
        log.error("%s", e)
        return 1
    wid = run_store.worker_id()
    log.info("Worker %s siap. Menunggu antrian run...", wid)
    sched = None
    if args.once:
        scheduler_round(db)
    else:
        sched = _Scheduler()
        sched.start()
    try:
        while True:
            run_store.worker_seen(db, wid)
            for stale_id in run_store.mark_stale_runs(db):
                log.warning("Run #%s ditandai INTERRUPTED (worker sebelumnya berhenti di tengah run).", stale_id)
            run_id = run_store.claim_next_run(db)
            if run_id is not None:
                execute_run(db, wid, run_id)
            elif args.once:
                break
            else:
                time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        log.info("Worker dihentikan.")
    finally:
        if sched:
            sched.stopped.set()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
