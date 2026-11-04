"""
Proyeksi data SEDERHANA yang dipakai SATU tampilan untuk semua peran (bahasa sehari-hari). Route membangun respons dari sini untuk
semua peran, lalu menambah blok `tech` (data lengkap) HANYA untuk operator/admin -- Viewer (termasuk admin yang pratinjau,
deps.is_viewer) hanya menerima bagian ini.

Prinsip: ALLOWLIST. Fungsi di sini membangun dict BARU yang hanya berisi field yang dibutuhkan bagian sederhana, bukan menghapus field
teknis dari respons lengkap -- field baru di data lengkap tidak pernah ikut bocor ke Viewer. Yang tidak pernah ada di sini: ID AWS
(Knowledge Base / Data Source / ingestion job), path & prefix S3, nama bucket, nama job Glue, query/argumen job, pesan error mentah,
deskripsi ingestion.

Status sederhana memakai kosakata kecil yang sama di semua halaman (frontend/src/components/kb.jsx: SimpleKbBadge, StepList).
"""

from __future__ import annotations

import sqlite3

from pipeline import kb_sync
from pipeline.app_settings import get_settings

# --- Knowledge Base ---------------------------------------------------------------------------------

_GLUE_STEP = {"ok": "done", "running": "running", "waiting": "waiting", "late": "problem", "failed": "problem"}
_KB_STEP = {"ok": "done", "running": "running", "waiting": "waiting", "none": "waiting", "pending": "problem",
            "failed": "problem"}
_INGESTION = {"COMPLETE": "done", "FAILED": "failed", "STOPPED": "stopped", "STARTING": "running", "IN_PROGRESS": "running",
              "STOPPING": "running"}


def _last_complete(db: sqlite3.Connection, target_id: int):
    return db.execute("SELECT * FROM kb_ingestions WHERE target_id = ? AND status = 'COMPLETE' "
                      "ORDER BY started_at DESC, id DESC LIMIT 1", (target_id,)).fetchone()


def kb_status(full: dict) -> str:
    """ready | updating | problem | none | off | unknown -- satu status yang dimengerti non-teknis."""
    flow = full.get("flow") or {}
    if not full["enabled"]:
        return "off"
    if full["running"] or (flow.get("glue") or {}).get("status") == "running":
        return "updating"
    if full["poll_error"] and not full["last"]:
        return "unknown"
    if full["health"] in ("failed", "stopped") or flow.get("overall") == "attention":
        return "problem"
    if full["health"] == "none":
        return "none"
    return "ready"


def kb_reason(full: dict) -> str | None:
    """Alasan singkat status 'problem' yang berasal dari jalur dokumen (gagal-nya pembaruan sendiri sudah tampil lewat last_attempt_failed)."""
    flow = full.get("flow") or {}
    glue, kb = flow.get("glue") or {}, flow.get("kb") or {}
    if glue.get("status") == "late":
        return "Pembersihan dokumen belum berjalan sesuai jadwal."
    if glue.get("status") == "failed":
        return "Pembersihan dokumen terakhir tidak berhasil."
    if kb.get("status") == "pending":
        return "Dokumen yang sudah dibersihkan belum masuk ke Knowledge Base."
    return None


def kb_target(db: sqlite3.Connection, row: sqlite3.Row, raw: dict | None = None, steps: bool = False) -> dict:
    full = kb_sync.target_view(db, row)
    done = _last_complete(db, row["id"])
    status = kb_status(full)
    out = {
        "id": full["id"],
        "title": full["title"] or full["name"],
        "status": status,
        "reason": kb_reason(full) if status == "problem" else None,
        "last_attempt_failed": bool(full["last"] and full["last"]["status"] in ("FAILED", "STOPPED")),
        "updated_at": done["updated_at"] if done else None,
        "documents": done["scanned"] if done else None,
        "failed_documents": (done["failed"] or 0) if done else 0,
        "last_change": ({"added": done["new_indexed"] or 0, "modified": done["modified_indexed"] or 0,
                         "deleted": done["deleted"] or 0} if done else None),
    }
    if steps:
        out["steps"] = kb_steps(full, raw, out["updated_at"])
    return out


def kb_steps(full: dict, raw: dict | None, kb_updated_at: str | None) -> list[dict] | None:
    """Tiga langkah jalur dokumen dalam bahasa sehari-hari; None untuk Knowledge Base tanpa jalur dokumen."""
    flow = full.get("flow")
    if not flow:
        return None
    glue, kb = flow.get("glue") or {}, flow.get("kb") or {}
    raw = raw or {}
    return [
        {"key": "copy", "label": "Salin dokumen dari Google Drive",
         "status": "done" if raw.get("latest_at") else "unknown", "at": raw.get("latest_at")},
        {"key": "clean", "label": "Pembersihan dokumen",
         "status": _GLUE_STEP.get(glue.get("status"), "unknown"), "at": glue.get("last_report_at")},
        {"key": "update", "label": "Pembaruan Knowledge Base",
         "status": "running" if full["running"] else _KB_STEP.get(kb.get("status"), "unknown"), "at": kb_updated_at},
    ]


def kb_ingestions(items: list[dict]) -> list[dict]:
    return [{"started_at": i["started_at"],
             "finished_at": i["updated_at"] if _INGESTION.get(i["status"]) != "running" else None,
             "status": _INGESTION.get(i["status"], "running"),
             "added": i["new_indexed"] or 0, "modified": i["modified_indexed"] or 0, "deleted": i["deleted"] or 0,
             "failed_documents": i["failed"] or 0} for i in items]


def kb_monitor(db: sqlite3.Connection) -> dict:
    s = get_settings(db, "kb.")
    return {"enabled": bool(s["kb.enabled"]), "last_poll_at": s["kb.last_poll_at"]}


def kb_raw(db: sqlite3.Connection) -> dict | None:
    return get_settings(db, "kb.")["kb.raw_status"]


# --- Riwayat proses (run) ---------------------------------------------------------------------------

_RUN_FIELDS = ("id", "status", "created_by", "created_at", "started_at", "finished_at", "start_date", "end_date", "retry_of",
               "cancel_requested", "total_items", "ok_items", "failed_items", "done_items", "job_count", "total_rows")
_ITEM_FIELDS = ("id", "seq", "target_date", "status", "rows", "elapsed_s", "started_at", "finished_at", "dq_status")


def run_summary(run: dict) -> dict:
    """Satu proses (daftar & kepala detail): tanpa opsi teknis (batch/kompresi/S3) dan tanpa pesan error mentah."""
    return {k: run.get(k) for k in _RUN_FIELDS} | {"has_error": bool(run.get("error"))}


def run_item(item: dict, labels: dict[str, str]) -> dict:
    """Satu data dalam proses: nama tampilan, bukan identifier/key S3/error mentah/rincian pemeriksaan."""
    return {k: item.get(k) for k in _ITEM_FIELDS} | {"label": labels.get(item["job_name"], item["job_name"]),
                                                     "has_error": bool(item.get("error"))}


# --- Daftar data (job) & jadwal ---------------------------------------------------------------------

def job_summary(row, kind: str, group: str | None, fresh: dict | None) -> dict:
    """
    Satu data (job) untuk semua peran: nama tampilan + kesegaran + irama. `name` = identifier untuk URL/aksi (bukan untuk
    ditampilkan sebagai judul). Tanpa template, argumen/query, path S3, catatan teknis, pengubah terakhir.
    """
    return {"name": row.name, "label": row.label, "enabled": row.enabled, "kind": kind,
            "cadence_effective": row.effective_cadence, "group": group, "freshness": fresh}


def job_runs(recent: list[dict]) -> list[dict]:
    """Proses terakhir satu data: tanpa key S3."""
    return [{k: r.get(k) for k in ("run_id", "target_date", "status", "rows", "finished_at", "created_by", "dq_status")}
            for r in recent]


_SCHEDULE_FIELDS = ("id", "name", "enabled", "description", "window_label", "next_run_at", "last_run_at", "last_run_id",
                    "last_run_status", "job_count")


def schedule(s: dict, labels: list[str]) -> dict:
    """Satu jadwal: kapan & hasil terakhir + nama tampilan data yang dicakup; tanpa selector (pola/modul), opsi, pesan mentah."""
    return {k: s.get(k) for k in _SCHEDULE_FIELDS} | {"job_labels": labels}


# --- Beranda ----------------------------------------------------------------------------------------

def dashboard_issue(issue: dict) -> dict:
    """Masalah DQ untuk Viewer: tanpa pesan pemeriksaan mentah (nama kolom, ambang)."""
    return {k: issue.get(k) for k in ("job_name", "label", "target_date", "run_id")}
