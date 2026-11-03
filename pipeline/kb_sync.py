"""
KB SYNC -- pemantauan Amazon Bedrock Knowledge Base (PRD v1.1 Fase 10).

Apa yang dikerjakan modul ini:
  * target KB (kb_targets): satu baris = satu pasangan Knowledge Base ID + Data Source ID yang dipantau;
  * riwayat ingestion (kb_ingestions): cermin ListIngestionJobs -- termasuk ingestion yang dimulai job Glue
    dokumen atau console AWS, bukan hanya yang dimulai aplikasi ini;
  * siklus pemantauan worker (run_cycle): sinkron riwayat + email bila ingestion gagal / ada dokumen gagal diindeks.

  * kesegaran tiga tahap jalur dokumen (rclone -> Glue -> KB) lewat S3 LIST, untuk target berjalur 'documents' -- lihat
    pipeline/kb_pipeline.py.

Yang SENGAJA tidak dikerjakan: memicu ingestion otomatis. Untuk KB dokumen, job Glue "documents cleansing and
chunking" sudah memulai ingestion sendiri (README §12); pemicu kedua dari sini berisiko ditolak Bedrock atau
memboroskan biaya embedding. Aplikasi hanya MEMBACA AWS (get/list). Sync manual (start_ingestion_job) menyusul
sebagai irisan terpisah.

Kredensial: pipeline/aws.py (IAM role / AWS_PROFILE lebih dulu). Pesan error ke UI = pesan sendiri per kode error.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from pipeline import aws, kb_pipeline, notify
from pipeline.app_settings import get_settings, set_settings
from pipeline.config import S3_BUCKET_NAME
from pipeline.job_store import utc_now, write_tx
from pipeline.scheduler import WIB

log = logging.getLogger("parquet_exporter")

TERMINAL = ("COMPLETE", "FAILED", "STOPPED")
IN_PROGRESS = ("STARTING", "IN_PROGRESS", "STOPPING")
PIPELINES = ("documents", "manual")
HISTORY_LIMIT = 30          # ingestion terbaru yang disinkronkan per target per siklus
MAX_REASON_FETCH = 5        # GetIngestionJob (alasan gagal) per target per siklus
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ID_RE = re.compile(r"^[0-9A-Za-z]{10}$")
DATASET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_BAD_SQL = "(status = 'FAILED' OR COALESCE(failed, 0) > 0)"


class KbError(Exception):
    """Isian target tidak valid / konflik -- pesan aman ditampilkan ke user (HTTP 400/409)."""


class KbAwsError(Exception):
    """Panggilan AWS gagal -- `str(e)` = pesan aplikasi (tanpa ARN/akun), `code` = kode error AWS."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _wrap(e: Exception) -> KbAwsError:
    return KbAwsError(aws.error_message(e), aws.error_code(e))


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _wib(iso: str | None) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")


# ---------------------------------------------------------------------------
# Pembacaan konfigurasi dari Bedrock
# ---------------------------------------------------------------------------

def list_data_sources(kb_id: str, client=None) -> dict:
    """{knowledge_base: {name, status}, data_sources: [{id, name, status}]} -- untuk memilih data source di UI."""
    if not ID_RE.match(kb_id or ""):
        raise KbError("Knowledge Base ID harus 10 karakter huruf/angka.")
    try:
        client = client or aws.client("bedrock-agent")
        kb = client.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        sources = []
        for page in client.get_paginator("list_data_sources").paginate(knowledgeBaseId=kb_id):
            sources += [{"id": d["dataSourceId"], "name": d.get("name"), "status": d.get("status")}
                        for d in page.get("dataSourceSummaries", [])]
    except aws.AwsNotConfigured as e:
        raise KbAwsError(aws.error_message(e), "AwsNotConfigured") from e
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e
    return {"knowledge_base": {"name": kb.get("name"), "status": kb.get("status")}, "data_sources": sources}


def describe_data_source(kb_id: str, ds_id: str, client=None) -> dict:
    """
    Validasi pasangan KB + data source ke Bedrock dan baca konfigurasinya:
    {kb_name, kb_status, ds_name, ds_status, type, prefixes, same_bucket}. Nama bucket TIDAK dikembalikan
    (hanya apakah sama dengan S3_BUCKET_NAME, karena kesegaran jalur dokumen membaca bucket itu).
    """
    for label, v in (("Knowledge Base ID", kb_id), ("Data Source ID", ds_id)):
        if not ID_RE.match(v or ""):
            raise KbError(f"{label} harus 10 karakter huruf/angka.")
    try:
        client = client or aws.client("bedrock-agent")
        kb = client.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        ds = client.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)["dataSource"]
    except aws.AwsNotConfigured as e:
        raise KbAwsError(aws.error_message(e), "AwsNotConfigured") from e
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e
    cfg = ds.get("dataSourceConfiguration") or {}
    s3 = cfg.get("s3Configuration") or {}
    bucket = (s3.get("bucketArn") or "").rsplit(":", 1)[-1]
    return {"kb_name": kb.get("name"), "kb_status": kb.get("status"), "ds_name": ds.get("name"),
            "ds_status": ds.get("status"), "type": cfg.get("type"), "prefixes": s3.get("inclusionPrefixes") or [],
            "same_bucket": bool(S3_BUCKET_NAME and bucket == S3_BUCKET_NAME)}


# ---------------------------------------------------------------------------
# Target (CRUD)
# ---------------------------------------------------------------------------

def get_target(conn: sqlite3.Connection, target_id: int):
    return conn.execute("SELECT * FROM kb_targets WHERE id = ?", (target_id,)).fetchone()


def list_targets(conn: sqlite3.Connection, enabled_only: bool = False) -> list:
    sql = "SELECT * FROM kb_targets" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY name"
    return conn.execute(sql).fetchall()


def _clean(data: dict, partial: bool = False) -> dict:
    """Rapikan & validasi isian target. partial=True (update): hanya kunci yang dikirim."""
    out = {}
    if not partial or "name" in data:
        name = str(data.get("name") or "").strip().lower()
        if not NAME_RE.match(name):
            raise KbError("Nama target: huruf kecil/angka/_/-, 1-40 karakter, diawali huruf atau angka.")
        out["name"] = name
    if not partial or "title" in data:
        out["title"] = (str(data.get("title") or "").strip() or None)
        if out["title"] and len(out["title"]) > 80:
            raise KbError("Judul maksimal 80 karakter.")
    if not partial or "pipeline" in data:
        pipeline = str(data.get("pipeline") or "manual")
        if pipeline not in PIPELINES:
            raise KbError("Jalur harus 'documents' atau 'manual'.")
        out["pipeline"] = pipeline
    if not partial or "dataset" in data or out.get("pipeline") == "documents":
        dataset = str(data.get("dataset") or "").strip() or None
        if dataset and not DATASET_RE.match(dataset):
            raise KbError("Dataset jalur dokumen: huruf/angka/._-, tanpa spasi atau garis miring.")
        out["dataset"] = dataset
    if not partial or "notes" in data:
        out["notes"] = (str(data.get("notes") or "").strip() or None)
    if "enabled" in data:
        out["enabled"] = 1 if data["enabled"] else 0
    pipeline = out.get("pipeline")
    if pipeline == "documents" and not out.get("dataset"):
        raise KbError("Jalur dokumen butuh nama dataset (folder di documents/.../v2/<dataset>/).")
    if pipeline == "manual":
        out["dataset"] = None
    return out


def create_target(conn: sqlite3.Connection, data: dict, by: str, client=None) -> dict:
    """Daftarkan target: validasi isian + ke Bedrock, simpan, lalu sinkron riwayat pertama. Return view target."""
    kb_id, ds_id = str(data.get("knowledge_base_id") or "").strip(), str(data.get("data_source_id") or "").strip()
    fields = _clean(data)
    info = describe_data_source(kb_id, ds_id, client)
    _check_dataset(fields["pipeline"], fields["dataset"])
    if conn.execute("SELECT 1 FROM kb_targets WHERE name = ?", (fields["name"],)).fetchone():
        raise KbError(f"Nama target '{fields['name']}' sudah dipakai.")
    if conn.execute("SELECT 1 FROM kb_targets WHERE knowledge_base_id = ? AND data_source_id = ?",
                    (kb_id, ds_id)).fetchone():
        raise KbError("Pasangan Knowledge Base + data source ini sudah terdaftar.")
    now = utc_now()
    prefix = info["prefixes"][0] if info["prefixes"] else ""
    cur = conn.execute(
        "INSERT INTO kb_targets (name, title, knowledge_base_id, data_source_id, source_prefix, pipeline, dataset, "
        "enabled, notes, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
        (fields["name"], fields["title"] or info["ds_name"], kb_id, ds_id, prefix, fields["pipeline"],
         fields["dataset"], fields["notes"], by, now, now))
    target_id = cur.lastrowid
    try:
        sync_target(conn, get_target(conn, target_id), client or aws.client("bedrock-agent"))
    except Exception as e:  # noqa: BLE001 -- target tetap tersimpan; riwayat menyusul di siklus worker
        log.warning("Sinkron awal target %s gagal: %s", fields["name"], aws.error_message(e))
    _refresh_flow(conn, get_target(conn, target_id))
    return target_view(conn, get_target(conn, target_id))


def _check_dataset(pipeline: str, dataset: str | None) -> None:
    """Jalur dokumen: folder v2/<dataset>/ harus ada di S3 (salah ketik = peringatan palsu 'Glue terlambat'). Tak bisa dicek = lolos."""
    if pipeline != "documents" or not dataset:
        return
    try:
        exists = kb_pipeline.dataset_exists(aws.client("s3"), dataset)
    except Exception:  # noqa: BLE001
        exists = None
    if exists is False:
        raise KbError(f"Folder dataset '{dataset}' tidak ditemukan di {kb_pipeline.V2_ROOT}. Periksa ejaan nama dataset.")


def update_target(conn: sqlite3.Connection, target_id: int, data: dict) -> dict:
    row = get_target(conn, target_id)
    if not row:
        raise KbError("Target tidak ditemukan.")
    merged = {"pipeline": row["pipeline"], "dataset": row["dataset"], **data}
    fields = _clean(merged, partial=True)
    if fields.get("pipeline") == "documents" and (fields.get("dataset") != row["dataset"] or row["pipeline"] != "documents"):
        _check_dataset("documents", fields.get("dataset"))
    fields.pop("name", None)  # nama tidak bisa diubah (kunci tampilan & laporan)
    allowed = {k: v for k, v in fields.items() if k in ("title", "pipeline", "dataset", "notes", "enabled")}
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        conn.execute(f"UPDATE kb_targets SET {sets}, updated_at = ? WHERE id = ?", (*allowed.values(), utc_now(), target_id))
        if fields.get("pipeline") != "documents":
            conn.execute("DELETE FROM kb_pipeline_status WHERE target_id = ?", (target_id,))
    return target_view(conn, get_target(conn, target_id))


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    if not get_target(conn, target_id):
        raise KbError("Target tidak ditemukan.")
    conn.execute("DELETE FROM kb_targets WHERE id = ?", (target_id,))  # kb_ingestions ikut terhapus (ON DELETE CASCADE)


# ---------------------------------------------------------------------------
# Tampilan
# ---------------------------------------------------------------------------

def _ingestion_dict(r: sqlite3.Row) -> dict:
    started, updated = r["started_at"], r["updated_at"]
    duration = None
    if started and updated and r["status"] in TERMINAL:
        duration = max(0, int((datetime.fromisoformat(updated) - datetime.fromisoformat(started)).total_seconds()))
    return {"id": r["id"], "ingestion_job_id": r["ingestion_job_id"], "status": r["status"], "started_at": started,
            "updated_at": updated, "duration_s": duration, "scanned": r["scanned"], "new_indexed": r["new_indexed"],
            "modified_indexed": r["modified_indexed"], "deleted": r["deleted"], "failed": r["failed"],
            "failure_reasons": json.loads(r["failure_reasons"]) if r["failure_reasons"] else [],
            "description": r["description"], "origin": r["origin"], "glue_scheduled": r["glue_scheduled"],
            "started_by": r["started_by"]}


def health_of(last: dict | None, running: int) -> str:
    """running | none | failed | warning | stopped | ok"""
    if running:
        return "running"
    if not last:
        return "none"
    if last["status"] == "FAILED":
        return "failed"
    if last["status"] == "STOPPED":
        return "stopped"
    return "warning" if (last["failed"] or 0) > 0 else "ok"


def list_ingestions(conn: sqlite3.Connection, target_id: int, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM kb_ingestions WHERE target_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
                        (target_id, limit)).fetchall()
    return [_ingestion_dict(r) for r in rows]


def target_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    last_row = conn.execute("SELECT * FROM kb_ingestions WHERE target_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
                            (row["id"],)).fetchone()
    last = _ingestion_dict(last_row) if last_row else None
    running = conn.execute("SELECT COUNT(*) FROM kb_ingestions WHERE target_id = ? AND status IN (?, ?, ?)",
                           (row["id"], *IN_PROGRESS)).fetchone()[0]
    return {"id": row["id"], "name": row["name"], "title": row["title"], "knowledge_base_id": row["knowledge_base_id"],
            "data_source_id": row["data_source_id"], "source_prefix": row["source_prefix"], "pipeline": row["pipeline"],
            "dataset": row["dataset"], "enabled": bool(row["enabled"]), "notes": row["notes"],
            "polled_at": row["polled_at"], "poll_error": row["poll_error"], "created_by": row["created_by"],
            "created_at": row["created_at"], "last": last, "running": running, "health": health_of(last, running),
            "flow": kb_pipeline.view(conn, row)}


# ---------------------------------------------------------------------------
# Sinkron riwayat ingestion
# ---------------------------------------------------------------------------

_UPSERT = (
    "INSERT INTO kb_ingestions (target_id, ingestion_job_id, status, started_at, updated_at, scanned, new_indexed, "
    "modified_indexed, deleted, failed, description, origin, notified_at, synced_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'external', ?, ?) "
    "ON CONFLICT(target_id, ingestion_job_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, "
    "scanned = excluded.scanned, new_indexed = excluded.new_indexed, modified_indexed = excluded.modified_indexed, "
    "deleted = excluded.deleted, failed = excluded.failed, description = excluded.description, "
    "synced_at = excluded.synced_at"
)


def sync_target(conn: sqlite3.Connection, target: sqlite3.Row, client, notify_failures: bool = True) -> dict:
    """
    Sinkronkan ingestion terbaru satu target (ListIngestionJobs) ke kb_ingestions, ambil alasan gagal, dan kirim
    email untuk ingestion yang baru terlihat gagal. Sinkron PERTAMA target (polled_at kosong) tidak mengirim email:
    riwayat lama bukan kejadian baru. Raise KbAwsError kalau AWS gagal.
    """
    tid = target["id"]
    first = target["polled_at"] is None
    try:
        jobs = client.list_ingestion_jobs(
            knowledgeBaseId=target["knowledge_base_id"], dataSourceId=target["data_source_id"],
            maxResults=HISTORY_LIMIT, sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"}
        ).get("ingestionJobSummaries", [])
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e
    now = utc_now()
    with write_tx(conn):
        for j in jobs:
            st = j.get("statistics") or {}
            conn.execute(_UPSERT, (tid, j["ingestionJobId"], j["status"], _iso(j.get("startedAt")), _iso(j.get("updatedAt")),
                                   st.get("numberOfDocumentsScanned"), st.get("numberOfNewDocumentsIndexed"),
                                   st.get("numberOfModifiedDocumentsIndexed"), st.get("numberOfDocumentsDeleted"),
                                   st.get("numberOfDocumentsFailed"), j.get("description"), now if first else None, now))
        # Selesai TANPA masalah -> tidak ada yang perlu diberitahukan (notified_at = penanda "sudah dievaluasi").
        conn.execute(f"UPDATE kb_ingestions SET notified_at = ? WHERE target_id = ? AND notified_at IS NULL "
                     f"AND status IN ('COMPLETE', 'STOPPED') AND NOT {_BAD_SQL}", (now, tid))
        conn.execute("UPDATE kb_targets SET polled_at = ?, poll_error = NULL WHERE id = ?", (now, tid))
    _fetch_failure_reasons(conn, target, client)
    notified = 0
    pending = conn.execute(f"SELECT * FROM kb_ingestions WHERE target_id = ? AND notified_at IS NULL "
                           f"AND status IN ('COMPLETE', 'FAILED') AND {_BAD_SQL} ORDER BY started_at", (tid,)).fetchall()
    if pending:
        if notify_failures and not first:
            try:
                notify.notify_kb_ingestions(conn, dict(target), [_notify_row(_ingestion_dict(r)) for r in pending])
            except Exception:  # noqa: BLE001 -- email tidak boleh menggagalkan sinkron
                log.exception("Notifikasi ingestion KB %s gagal diproses.", target["name"])
        conn.execute(f"UPDATE kb_ingestions SET notified_at = ? WHERE target_id = ? AND notified_at IS NULL "
                     f"AND status IN ('COMPLETE', 'FAILED') AND {_BAD_SQL}", (now, tid))
        notified = len(pending)
    running = conn.execute("SELECT COUNT(*) FROM kb_ingestions WHERE target_id = ? AND status IN (?, ?, ?)",
                           (tid, *IN_PROGRESS)).fetchone()[0]
    return {"synced": len(jobs), "running": running, "notified": notified}


def _fetch_failure_reasons(conn: sqlite3.Connection, target: sqlite3.Row, client) -> None:
    rows = conn.execute(f"SELECT id, ingestion_job_id FROM kb_ingestions WHERE target_id = ? AND failure_reasons IS NULL "
                        f"AND status IN ('COMPLETE', 'FAILED') AND {_BAD_SQL} ORDER BY started_at DESC LIMIT ?",
                        (target["id"], MAX_REASON_FETCH)).fetchall()
    for r in rows:
        try:
            job = client.get_ingestion_job(knowledgeBaseId=target["knowledge_base_id"],
                                           dataSourceId=target["data_source_id"],
                                           ingestionJobId=r["ingestion_job_id"])["ingestionJob"]
            reasons = [str(x)[:300] for x in (job.get("failureReasons") or [])][:10]
        except Exception as e:  # noqa: BLE001 -- alasan hanya pelengkap
            log.warning("Alasan gagal ingestion %s tidak terbaca: %s", target["name"], aws.error_message(e))
            continue
        conn.execute("UPDATE kb_ingestions SET failure_reasons = ? WHERE id = ?", (json.dumps(reasons), r["id"]))


def _notify_row(i: dict) -> tuple:
    stats = (f"{i['new_indexed'] or 0} baru, {i['modified_indexed'] or 0} diubah, {i['deleted'] or 0} dihapus, "
             f"{i['failed'] or 0} gagal")
    why = "; ".join(i["failure_reasons"])[:300] if i["failure_reasons"] else ""
    return (_wib(i["started_at"]), "gagal" if i["status"] == "FAILED" else "dokumen gagal diindeks", stats, why)


# ---------------------------------------------------------------------------
# Siklus worker
# ---------------------------------------------------------------------------

def _refresh_flow(conn: sqlite3.Connection, target: sqlite3.Row, result: dict | None = None) -> None:
    """Segarkan kesegaran tiga tahap (LIST S3) target berjalur dokumen. Tidak melempar error."""
    if target["pipeline"] != "documents" or not target["dataset"]:
        return
    try:
        s3 = aws.client("s3")
    except Exception as e:  # noqa: BLE001
        kb_pipeline.record_error(conn, target["id"], aws.error_message(e))
        return
    r = kb_pipeline.refresh(conn, target, s3)
    if result is not None:
        result["flow_alerts"] += r["alerts"]


def sync_all(conn: sqlite3.Connection, by: str = "worker", client=None) -> dict:
    """
    Sinkronkan semua target aktif. Return {at, by, targets, ok, errors[{target, error}], error, active, notified, flow_alerts};
    `error` = kegagalan menyeluruh (kredensial/jaringan) yang menghentikan siklus. Tidak melempar error.
    """
    targets = list_targets(conn, enabled_only=True)
    result = {"at": utc_now(), "by": by, "targets": len(targets), "ok": 0, "errors": [], "error": None,
              "active": False, "notified": 0, "flow_alerts": 0}
    if not targets:
        return result
    try:
        client = client or aws.client("bedrock-agent")
    except Exception as e:  # noqa: BLE001
        result["error"] = aws.error_message(e)
        for t in targets:
            conn.execute("UPDATE kb_targets SET poll_error = ? WHERE id = ?", (result["error"], t["id"]))
        return result
    for t in targets:
        try:
            r = sync_target(conn, t, client)
        except KbAwsError as e:
            conn.execute("UPDATE kb_targets SET poll_error = ? WHERE id = ?", (str(e), t["id"]))
            result["errors"].append({"target": t["name"], "error": str(e)})
            if e.code in aws.FATAL:
                result["error"] = str(e)
                break
            continue
        result["ok"] += 1
        result["active"] = result["active"] or r["running"] > 0
        result["notified"] += r["notified"]
    if any(t["pipeline"] == "documents" for t in targets) and not result["error"]:
        try:
            kb_pipeline.refresh_raw(conn, aws.client("s3"))
        except Exception:  # noqa: BLE001 -- raw/ hanya informasi
            log.warning("Pindai raw/ gagal diproses.", exc_info=True)
        for t in targets:
            _refresh_flow(conn, t, result)
    return result


def run_cycle(conn: sqlite3.Connection, now: datetime | None = None) -> dict | None:
    """
    Dipanggil thread scheduler worker (tiap ~20 detik). None kalau nonaktif / tanpa target / belum waktunya.
    Jeda = kb.poll_active_seconds bila ada ingestion berjalan di siklus sebelumnya, selain itu kb.poll_idle_seconds.
    """
    s = get_settings(conn, "kb.")
    if not s["kb.enabled"]:
        return None
    now = now or datetime.now(timezone.utc)
    last = s["kb.last_poll_at"]
    interval = s["kb.poll_active_seconds"] if s["kb.active"] else s["kb.poll_idle_seconds"]
    if last and (now - datetime.fromisoformat(last)).total_seconds() < interval:
        return None
    if not list_targets(conn, enabled_only=True):
        return None
    set_settings(conn, {"kb.last_poll_at": _iso(now)}, "worker")  # tandai dulu: gagal pun menunggu jeda berikutnya
    result = sync_all(conn, by="worker")
    set_settings(conn, {"kb.active": result["active"], "kb.last_result": result}, "worker")
    return result


def sync_now(conn: sqlite3.Connection, target_id: int) -> dict:
    """Sinkron satu target sekarang (tombol 'Muat ulang riwayat'). Raise KbError / KbAwsError."""
    row = get_target(conn, target_id)
    if not row:
        raise KbError("Target tidak ditemukan.")
    try:
        client = aws.client("bedrock-agent")
    except Exception as e:  # noqa: BLE001
        raise KbAwsError(aws.error_message(e), aws.error_code(e)) from e
    try:
        r = sync_target(conn, row, client)
    except KbAwsError as e:
        conn.execute("UPDATE kb_targets SET poll_error = ? WHERE id = ?", (str(e), target_id))
        raise
    if row["pipeline"] == "documents":
        try:
            kb_pipeline.refresh_raw(conn, aws.client("s3"), force=True)
        except Exception:  # noqa: BLE001
            log.warning("Pindai raw/ gagal diproses.", exc_info=True)
        _refresh_flow(conn, row)
    return r
