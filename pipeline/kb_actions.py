"""
KB ACTIONS -- proses manual jalur dokumen (PRD v1.1 Fase 10 irisan 4): tombol "Jalankan pembersihan" (job Glue cleansing) dan
"Perbarui sekarang" (ingestion Knowledge Base).

Alur: web hanya MENGANTRIKAN permintaan (request) -- tidak pernah memanggil AWS. Worker (thread scheduler, tiap ~20 detik) mengambil
antrian (process), memeriksa ULANG syarat dengan data AWS terbaru (evaluate_guards), lalu memulai prosesnya. Permintaan yang tidak
diproses dalam QUEUE_EXPIRY dibatalkan supaya tidak "tiba-tiba jalan" lama kemudian.

  glue_run  : glue:StartJobRun job dataset (argumen default job). Glue sendiri memulai ingestion di akhir run, jadi ini = proses +
              sync. Syarat: tidak ada run aktif (MaxConcurrentRuns=1), tidak ada ingestion aktif di KB (kalau ada, run Glue
              langsung berhenti 'ingestion_busy' tanpa memproses dokumen).
  ingestion : bedrock:StartIngestionJob LANGSUNG (keputusan user 2026-11-03). Syarat meniru koordinator job Glue
              (prep/glue_sync.coordinate: "All writers and sync triggers for these KBs must use this coordinator"):
                1. tidak ada run Glue aktif untuk dataset itu (bila status run tidak terbaca -> ditolak),
                2. tidak ada state/glue/journal.json (publikasi belum tuntas -> hasil setengah jadi ikut terindeks),
                3. tidak ada ingestion aktif di data source MANA PUN pada KB itu (sama dengan cek Glue),
                4. tidak dalam kb.manual_block_minutes sebelum jadwal Glue (ingestion yang masih jalan saat Glue mulai membuat
                   run terjadwal berhenti 'ingestion_busy').
              Target tanpa jalur dokumen (mis. few-shot-sql): hanya syarat 3. Efek samping yang diketahui: Glue tidak tahu ingestion
              manual ini, sehingga di akhir run berikutnya bisa memulai satu ingestion lagi (inkremental -- murah).
  rclone    : disiapkan untuk irisan 4b (jembatan host), belum ada di sini.

Ingestion yang dimulai di sini diberi description berawalan kb_sync.APP_DESCRIPTION_PREFIX -> riwayat menandainya asal 'app'.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from pipeline import aws, kb_pipeline, kb_sync
from pipeline.app_settings import get_settings, set_settings
from pipeline.config import S3_BUCKET_NAME
from pipeline.job_store import utc_now
from pipeline.scheduler import WIB

log = logging.getLogger("parquet_exporter")

# Label tampilan (istilah sederhana, sama untuk semua peran); kunci = nilai kolom kb_actions.action.
ACTIONS = {"glue_run": "Pembersihan dokumen", "ingestion": "Pembaruan Knowledge Base"}
INGESTION_ACTIVE = ("STARTING", "IN_PROGRESS", "STOPPING")
QUEUE_EXPIRY = timedelta(minutes=10)
# Eksekusi hanya beberapa detik; baris 'running' setua ini = worker berhenti di tengah proses (tanpa ini tombol terkunci selamanya).
STUCK_AFTER = timedelta(minutes=30)
# Kode error AWS yang berarti "sedang ada proses lain" -> ditolak (bukan gagal teknis).
BUSY_CODES = {"ConflictException", "ConcurrentRunsExceededException"}


class ActionError(Exception):
    """Permintaan tidak bisa diantrikan -- pesan aman ditampilkan ke user. conflict=True -> HTTP 409."""

    def __init__(self, message: str, conflict: bool = False):
        super().__init__(message)
        self.conflict = conflict


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def action_view(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "action": r["action"], "label": ACTIONS.get(r["action"], r["action"]), "status": r["status"],
            "requested_by": r["requested_by"], "requested_at": r["requested_at"], "finished_at": r["finished_at"],
            "external_id": r["external_id"], "message": r["message"]}


def list_actions(conn: sqlite3.Connection, target_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute("SELECT * FROM kb_actions WHERE target_id = ? ORDER BY id DESC LIMIT ?", (target_id, limit)).fetchall()
    return [action_view(r) for r in rows]


def _open(conn: sqlite3.Connection, target_id: int, action: str):
    return conn.execute("SELECT * FROM kb_actions WHERE target_id = ? AND action = ? AND status IN ('queued', 'running') "
                        "ORDER BY id DESC LIMIT 1", (target_id, action)).fetchone()


def in_block_window(now: datetime, run_time, minutes: int) -> bool:
    """Sekarang berada di `minutes` menit SEBELUM jadwal Glue (WIB)?"""
    now_wib = now.astimezone(WIB)
    run_at = datetime.combine(now_wib.date(), run_time, tzinfo=WIB)
    if run_at < now_wib:
        run_at += timedelta(days=1)
    return run_at - now_wib <= timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Ketersediaan tombol (web) -- dari fakta tersimpan saja, TANPA memanggil AWS
# ---------------------------------------------------------------------------

def availability(conn: sqlite3.Connection, target: sqlite3.Row, worker_online: bool = True, now: datetime | None = None) -> dict:
    """
    {aksi: {enabled, reason}} untuk UI. Hanya prakiraan dari data tersimpan (status terakhir yang dibaca worker); keputusan akhir
    tetap di worker dengan data AWS terbaru (evaluate_guards).
    """
    now = now or _now()
    s = get_settings(conn, "kb.")
    documents = target["pipeline"] == "documents" and bool(target["dataset"])
    flow = kb_pipeline.view(conn, target, s, now) if documents else None
    running_ingestion = conn.execute("SELECT 1 FROM kb_ingestions WHERE target_id = ? AND status IN (?, ?, ?) LIMIT 1",
                                     (target["id"], *INGESTION_ACTIVE)).fetchone() is not None
    glue = (flow or {}).get("glue") or {}
    out = {}
    for action in (("glue_run", "ingestion") if documents else ("ingestion",)):
        reason = None
        if not target["enabled"]:
            reason = "Target nonaktif."
        elif not worker_online:
            reason = "Worker tidak aktif — permintaan tidak akan diproses."
        elif _open(conn, target["id"], action):
            reason = "Permintaan sebelumnya masih menunggu diproses worker."
        elif documents and (flow is None or flow["checked_at"] is None or flow["stale"]):
            reason = "Status jalur dokumen belum diperbarui — tekan “Muat ulang riwayat”."
        elif documents and flow.get("glue_error"):
            reason = "Status run Glue tidak terbaca — tidak bisa memastikan Glue sedang tidak berjalan."
        elif documents and glue.get("status") == "running":
            reason = "Glue sedang berproses — Glue memulai ingestion sendiri di akhir run."
        elif running_ingestion:
            reason = "Ada ingestion Knowledge Base yang sedang berjalan."
        elif action == "ingestion" and documents and flow.get("journal_at"):
            reason = "Ada publikasi Glue yang belum tuntas (journal) — tunggu run Glue berikutnya memulihkannya."
        elif action == "ingestion" and documents and in_block_window(now, kb_pipeline.hhmm(s["kb.glue_run_time"]),
                                                                       s["kb.manual_block_minutes"]):
            reason = (f"Kurang dari {s['kb.manual_block_minutes']} menit sebelum jadwal Glue ({s['kb.glue_run_time']} WIB) — "
                      f"ingestion yang masih berjalan saat itu membuat run Glue terjadwal berhenti.")
        out[action] = {"enabled": reason is None, "reason": reason}
    return out


def request(conn: sqlite3.Connection, target_id: int, action: str, by: str, worker_online: bool = True) -> dict:
    """Antrikan permintaan (web). Ditolak dengan ActionError bila tidak tersedia menurut fakta tersimpan."""
    target = kb_sync.get_target(conn, target_id)
    if not target:
        raise ActionError("Target tidak ditemukan.")
    if action not in ACTIONS:
        raise ActionError("Aksi tidak dikenal.")
    avail = availability(conn, target, worker_online)
    if action not in avail:
        raise ActionError("Aksi ini hanya untuk target berjalur dokumen.")
    if not avail[action]["enabled"]:
        raise ActionError(avail[action]["reason"], conflict=True)
    cur = conn.execute("INSERT INTO kb_actions (target_id, action, status, requested_by, requested_at) VALUES (?, ?, 'queued', ?, ?)",
                       (target_id, action, by, utc_now()))
    return action_view(conn.execute("SELECT * FROM kb_actions WHERE id = ?", (cur.lastrowid,)).fetchone())


# ---------------------------------------------------------------------------
# Pemeriksaan syarat (worker) -- data AWS terbaru, hanya baca
# ---------------------------------------------------------------------------

def active_ingestion_in_kb(bedrock, kb_id: str) -> bool:
    """
    Ada ingestion aktif di data source MANA PUN pada KB ini? (maksud sama dengan prep/glue_sync.active_jobs)
    SATU status per panggilan: filter STATUS EQ dengan beberapa nilai sekaligus mengembalikan daftar KOSONG walau ada ingestion
    IN_PROGRESS (terbukti ke Bedrock asli 3 Nov 2026, KB wm) -- satu nilai per filter terbukti benar.
    """
    for page in bedrock.get_paginator("list_data_sources").paginate(knowledgeBaseId=kb_id):
        for ds in page.get("dataSourceSummaries", []):
            for status in INGESTION_ACTIVE:
                resp = bedrock.list_ingestion_jobs(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"], maxResults=5,
                                                   filters=[{"attribute": "STATUS", "operator": "EQ", "values": [status]}])
                if any(j["status"] in INGESTION_ACTIVE for j in resp.get("ingestionJobSummaries", [])):
                    return True
    return False


def evaluate_guards(conn: sqlite3.Connection, target: sqlite3.Row, action: str, bedrock, glue, s3,
                    now: datetime | None = None) -> str | None:
    """Alasan penolakan (pesan aplikasi), atau None bila boleh dimulai. HANYA MEMBACA AWS."""
    now = now or _now()
    s = get_settings(conn, "kb.")
    documents = target["pipeline"] == "documents" and bool(target["dataset"])
    if action == "glue_run" and not documents:
        return "Aksi ini hanya untuk target berjalur dokumen."
    if documents:
        job = kb_pipeline.glue_job_name(s["kb.glue_job_pattern"], target["dataset"])
        try:
            run = kb_pipeline.latest_glue_run(glue, job)
        except Exception as e:  # noqa: BLE001
            return f"Status run Glue tidak terbaca ({aws.error_message(e)})."
        if run and run["state"] in kb_pipeline.ACTIVE_RUN:
            return "Glue sedang berproses — Glue memulai ingestion sendiri di akhir run."
    try:
        if active_ingestion_in_kb(bedrock, target["knowledge_base_id"]):
            return "Ada ingestion Knowledge Base yang sedang berjalan."
    except Exception as e:  # noqa: BLE001
        return f"Status ingestion tidak terbaca ({aws.error_message(e)})."
    if action == "ingestion" and documents:
        try:
            if kb_pipeline.journal_modified(s3, target["dataset"]):
                return "Ada publikasi Glue yang belum tuntas (journal) — tunggu run Glue berikutnya memulihkannya."
        except Exception as e:  # noqa: BLE001
            return f"Jurnal publikasi Glue tidak terbaca ({aws.error_message(e)})."
        if in_block_window(now, kb_pipeline.hhmm(s["kb.glue_run_time"]), s["kb.manual_block_minutes"]):
            return f"Kurang dari {s['kb.manual_block_minutes']} menit sebelum jadwal Glue ({s['kb.glue_run_time']} WIB)."
    return None


# ---------------------------------------------------------------------------
# Eksekusi (worker)
# ---------------------------------------------------------------------------

def _finish(conn: sqlite3.Connection, action_id: int, status: str, message: str, external_id: str | None = None) -> None:
    conn.execute("UPDATE kb_actions SET status = ?, message = ?, external_id = ?, finished_at = ? WHERE id = ?",
                 (status, message[:500], external_id, utc_now(), action_id))


def _start_ingestion(conn: sqlite3.Connection, target: sqlite3.Row, by: str, bedrock) -> str:
    job = bedrock.start_ingestion_job(
        knowledgeBaseId=target["knowledge_base_id"], dataSourceId=target["data_source_id"],
        clientToken=uuid.uuid4().hex + uuid.uuid4().hex,
        description=f"{kb_sync.APP_DESCRIPTION_PREFIX} sync manual oleh {by}"[:200])["ingestionJob"]
    now = utc_now()
    conn.execute(
        "INSERT INTO kb_ingestions (target_id, ingestion_job_id, status, started_at, updated_at, description, origin, started_by, "
        "origin_checked, synced_at) VALUES (?, ?, ?, ?, ?, ?, 'app', ?, 1, ?) ON CONFLICT(target_id, ingestion_job_id) DO UPDATE SET "
        "origin = 'app', started_by = excluded.started_by, origin_checked = 1",
        (target["id"], job["ingestionJobId"], job.get("status", "STARTING"), _iso(job.get("startedAt")) or now,
         _iso(job.get("updatedAt")) or now, job.get("description"), by, now))
    return job["ingestionJobId"]


def execute(conn: sqlite3.Connection, row: sqlite3.Row, clients, now: datetime | None = None) -> str:
    """Jalankan satu permintaan yang sudah diklaim (status 'running'). Return status akhir. Tidak melempar error."""
    target = kb_sync.get_target(conn, row["target_id"])
    if not target or not target["enabled"]:
        _finish(conn, row["id"], "rejected", "Target tidak ada atau nonaktif.")
        return "rejected"
    try:
        bedrock, glue, s3 = clients()
    except Exception as e:  # noqa: BLE001
        _finish(conn, row["id"], "failed", aws.error_message(e))
        return "failed"
    reason = evaluate_guards(conn, target, row["action"], bedrock, glue, s3, now)
    if reason:
        _finish(conn, row["id"], "rejected", reason)
        return "rejected"
    try:
        if row["action"] == "ingestion":
            ext = _start_ingestion(conn, target, row["requested_by"], bedrock)
            msg = "Ingestion Knowledge Base dimulai."
        else:
            job = kb_pipeline.glue_job_name(get_settings(conn, "kb.")["kb.glue_job_pattern"], target["dataset"])
            ext = glue.start_job_run(JobName=job)["JobRunId"]
            msg = f"Run Glue {job} dimulai. Glue memulai ingestion sendiri di akhir run bila ada dokumen berubah."
    except Exception as e:  # noqa: BLE001
        code = aws.error_code(e)
        status = "rejected" if code in BUSY_CODES else "failed"
        _finish(conn, row["id"], status, aws.error_message(e))
        return status
    _finish(conn, row["id"], "started", msg, ext)
    log.info("Proses manual %s untuk %s dimulai oleh %s (%s).", row["action"], target["name"], row["requested_by"], ext)
    set_settings(conn, {"kb.active": True, "kb.last_poll_at": None}, "worker")   # siklus berikutnya segera, jeda aktif
    try:
        if row["action"] == "ingestion":
            kb_sync.sync_target(conn, kb_sync.get_target(conn, target["id"]), bedrock)
        kb_sync.refresh_flow(conn, kb_sync.get_target(conn, target["id"]))
    except Exception:  # noqa: BLE001 -- status menyusul di siklus pemantauan berikutnya
        log.warning("Penyegaran status setelah proses manual %s tertunda.", target["name"], exc_info=True)
    return "started"


def _clients():
    return aws.client("bedrock-agent"), aws.client("glue"), aws.client("s3")


def process(conn: sqlite3.Connection, now: datetime | None = None, clients=_clients) -> dict | None:
    """Thread scheduler worker: batalkan permintaan basi, lalu kerjakan antrian. None bila tidak ada apa pun."""
    now = now or _now()
    expired = conn.execute("UPDATE kb_actions SET status = 'rejected', finished_at = ?, message = ? WHERE status = 'queued' "
                           "AND requested_at < ?", (utc_now(), "Tidak diproses worker dalam 10 menit — ulangi bila masih perlu.",
                                                     _iso(now - QUEUE_EXPIRY))).rowcount
    # AWS mungkin sudah menerima panggilannya sebelum worker berhenti -> 'failed' + minta cek dulu, bukan 'rejected'.
    stuck = conn.execute("UPDATE kb_actions SET status = 'failed', finished_at = ?, message = ? WHERE status = 'running' "
                         "AND requested_at < ?", (utc_now(), "Worker berhenti saat memproses permintaan ini — cek riwayat "
                                                  "ingestion/status Glue sebelum mengulang.", _iso(now - STUCK_AFTER))).rowcount
    queued = conn.execute("SELECT id FROM kb_actions WHERE status = 'queued' ORDER BY id").fetchall()
    if not queued and not expired and not stuck:
        return None
    result = {"started": 0, "rejected": 0, "failed": stuck, "expired": expired}
    for (action_id,) in queued:
        # Klaim atomik: hanya satu worker yang memproses satu permintaan.
        if conn.execute("UPDATE kb_actions SET status = 'running' WHERE id = ? AND status = 'queued'", (action_id,)).rowcount != 1:
            continue
        row = conn.execute("SELECT * FROM kb_actions WHERE id = ?", (action_id,)).fetchone()
        result[execute(conn, row, clients, now)] += 1
    return result
