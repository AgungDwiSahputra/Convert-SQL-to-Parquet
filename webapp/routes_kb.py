"""
Knowledge Base (PRD v1.1 Fase 10): target KB Bedrock yang dipantau + riwayat ingestion-nya.

Semua pembacaan AWS di sini hanya get/list (cepat, beberapa detik). Memulai ingestion TIDAK ada di route ini:
untuk KB dokumen, job Glue sudah memicunya (pipeline/kb_sync.py). Viewer boleh melihat; muat ulang riwayat =
operator; mendaftarkan/mengubah/menghapus target = admin.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from pipeline import aws, kb_sync
from pipeline.app_settings import get_settings
from webapp.deps import current_user, get_db, require_role

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
def list_all(_user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return {"targets": [kb_sync.target_view(db, r) for r in kb_sync.list_targets(db)], "monitor": _monitor(db)}


@router.post("/lookup")
def lookup(body: LookupIn, _admin: dict = Depends(require_role("admin"))):
    """Cek Knowledge Base ID ke Bedrock dan daftar data source-nya (untuk dipilih di form pendaftaran)."""
    return _guard(kb_sync.list_data_sources, body.knowledge_base_id.strip())


@router.get("/{target_id}")
def detail(target_id: int, _user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = kb_sync.get_target(db, target_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target tidak ditemukan.")
    return {"target": kb_sync.target_view(db, row), "ingestions": kb_sync.list_ingestions(db, target_id, 50),
            "monitor": _monitor(db)}


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
