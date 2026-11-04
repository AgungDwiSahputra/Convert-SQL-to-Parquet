"""
Knowledge Base (PRD v1.1 Fase 10): target KB Bedrock yang dipantau + riwayat ingestion-nya.

Semua pembacaan AWS di sini hanya get/list (cepat, beberapa detik). Proses manual (Cleansing Glue / Sync KB) hanya
DIANTRIKAN di sini -- worker yang memeriksa syarat dengan data AWS terbaru lalu memulainya (pipeline/kb_actions.py). Satu bentuk
respons untuk semua peran: bagian sederhana (webapp/simple_view.py) + blok `tech`/`actions`/`availability` hanya untuk operator/admin
(Viewer tidak menerima ID AWS, prefix S3, error mentah). Muat ulang riwayat & proses manual = operator; daftar/ubah/hapus = admin.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from pipeline import aws, kb_actions, kb_sync, run_store
from pipeline.app_settings import get_settings
from webapp import simple_view
from webapp.deps import current_user, get_db, is_viewer, require_role

router = APIRouter()


class LookupIn(BaseModel):
    knowledge_base_id: str = Field(min_length=1, max_length=20)


class TargetIn(BaseModel):
    name: str = Field(max_length=60)
    title: str | None = Field(None, max_length=120)
    knowledge_base_id: str = Field(min_length=1, max_length=20)
    data_source_id: str = Field(min_length=1, max_length=20)
    pipeline: str = "manual"
    dataset: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=500)


class ActionIn(BaseModel):
    action: str = Field(max_length=20)


class TargetPatch(BaseModel):
    title: str | None = Field(None, max_length=120)
    pipeline: str | None = None
    dataset: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=500)
    enabled: bool | None = None


def _guard(fn, *args, **kwargs):
    """Jalankan fungsi kb_sync; ubah error domain menjadi HTTP error dengan pesan aplikasi (bukan pesan AWS)."""
    try:
        return fn(*args, **kwargs)
    except kb_sync.KbError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except kb_sync.KbAwsError as e:
        code = (status.HTTP_503_SERVICE_UNAVAILABLE if e.code == "AwsNotConfigured"
                else status.HTTP_403_FORBIDDEN if e.code in aws.DENIED else status.HTTP_502_BAD_GATEWAY)
        raise HTTPException(code, str(e)) from e


def _monitor(db: sqlite3.Connection) -> dict:
    s = get_settings(db, "kb.")
    return {"enabled": s["kb.enabled"], "last_poll_at": s["kb.last_poll_at"], "last_result": s["kb.last_result"],
            "poll_idle_seconds": s["kb.poll_idle_seconds"], "poll_active_seconds": s["kb.poll_active_seconds"],
            "auth_mode": aws.auth_mode(), "raw": s["kb.raw_status"],
            "schedule": {"run_time": s["kb.glue_run_time"], "deadline": s["kb.glue_deadline"],
                         "pending_minutes": s["kb.pending_minutes"]}}


@router.get("")
def list_all(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    """
    SATU bentuk respons untuk semua peran (satu tampilan): bagian sederhana (simple_view) untuk semua; blok `tech` (data lengkap)
    HANYA untuk operator/admin. Viewer juga tidak melihat target nonaktif.
    """
    viewer = is_viewer(user)
    targets = []
    for r in kb_sync.list_targets(db, enabled_only=viewer):
        t = simple_view.kb_target(db, r)
        if not viewer:
            t["tech"] = {"name": r["name"], "poll_error": r["poll_error"]}
        targets.append(t)
    monitor = simple_view.kb_monitor(db)
    if not viewer:
        monitor["tech"] = _monitor(db)
    return {"targets": targets, "monitor": monitor}


@router.post("/lookup")
def lookup(body: LookupIn, _admin: dict = Depends(require_role("admin"))):
    """Cek Knowledge Base ID ke Bedrock dan daftar data source-nya (untuk dipilih di form pendaftaran)."""
    return _guard(kb_sync.list_data_sources, body.knowledge_base_id.strip())


@router.get("/{target_id}")
def detail(target_id: int, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = kb_sync.get_target(db, target_id)
    viewer = is_viewer(user)
    if not row or (viewer and not row["enabled"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target tidak ditemukan.")
    out = {"monitor": simple_view.kb_monitor(db),
           "target": simple_view.kb_target(db, row, simple_view.kb_raw(db), steps=True),
           "ingestions": simple_view.kb_ingestions(kb_sync.list_ingestions(db, target_id, 10))}
    if viewer:
        return out
    return out | {"tech": {"target": kb_sync.target_view(db, row), "ingestions": kb_sync.list_ingestions(db, target_id, 50),
                           "monitor": _monitor(db)},
                  "actions": kb_actions.list_actions(db, target_id, 10),
                  "availability": kb_actions.availability(db, row, run_store.worker_online(db))}


@router.post("/{target_id}/actions")
def request_action(target_id: int, body: ActionIn, user: dict = Depends(require_role("operator")),
                   db: sqlite3.Connection = Depends(get_db)):
    """Antrikan proses manual (glue_run / ingestion). Worker memeriksa ulang syarat dengan data AWS terbaru sebelum memulai."""
    try:
        action = kb_actions.request(db, target_id, body.action, user["username"], run_store.worker_online(db))
    except kb_actions.ActionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT if e.conflict else status.HTTP_400_BAD_REQUEST, str(e)) from e
    row = kb_sync.get_target(db, target_id)
    return {"action": action, "actions": kb_actions.list_actions(db, target_id, 10),
            "availability": kb_actions.availability(db, row, run_store.worker_online(db))}


@router.post("")
def create(body: TargetIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    return _guard(kb_sync.create_target, db, body.model_dump(), admin["username"])


@router.put("/{target_id}")
def update(target_id: int, body: TargetPatch, _admin: dict = Depends(require_role("admin")),
           db: sqlite3.Connection = Depends(get_db)):
    return _guard(kb_sync.update_target, db, target_id, body.model_dump(exclude_unset=True))


@router.delete("/{target_id}")
def delete(target_id: int, _admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    _guard(kb_sync.delete_target, db, target_id)
    return {"ok": True}


@router.post("/{target_id}/refresh")
def refresh(target_id: int, _op: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    """Sinkron riwayat ingestion target ini sekarang (tanpa menunggu siklus worker)."""
    result = _guard(kb_sync.sync_now, db, target_id)
    return {"result": result, "target": kb_sync.target_view(db, kb_sync.get_target(db, target_id)),
            "ingestions": kb_sync.list_ingestions(db, target_id, 50)}
