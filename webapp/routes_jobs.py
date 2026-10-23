"""
Daftar, detail, tambah, ubah, aktif/nonaktif & uji coba job.

Lihat: viewer+. Ubah: operator+ untuk template bronze_*, admin untuk template khusus admin
(lihat job_validation.TEMPLATE_SPECS). Semua perubahan tercatat di job_history.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from job_store import (
    JobRow, build_jobs, create_job, default_target_date, get_job_row, job_history, load_job_rows,
    set_job_enabled, update_job,
)
from job_templates import STRDATE_FORMATS, _SIMPLE_ARROW_TYPES, build_job, s3_folder, s3_target_info
from job_validation import TEMPLATE_SPECS, can_edit, validate_job
from webapp.catalog import CATALOG, CatalogError, TestRunBusy, test_run
from webapp.deps import current_user, get_db, require_role

router = APIRouter()


class JobDraft(BaseModel):
    name: str = ""
    template: str
    args: dict
    notes: str | None = Field(default=None, max_length=20_000)
    original_name: str | None = None   # diisi saat edit
    confirm_s3_move: bool = False


class JobCreate(JobDraft):
    enabled: bool = False


class EnabledIn(BaseModel):
    enabled: bool


class TestRunIn(BaseModel):
    name: str = ""
    template: str
    args: dict
    original_name: str | None = None
    target_date: date | None = None
    limit: int = Field(100, ge=1, le=500)


def _job_dict(row: JobRow, job, role: str) -> dict:
    try:
        s3_key = job.s3_key({}) if job.s3_key else None
    except KeyError:  # layout mingguan: path baru diketahui dari data
        s3_key = None
    return {
        "name": row.name,
        "template": row.template,
        "template_label": TEMPLATE_SPECS.get(row.template, {}).get("label", row.template),
        "enabled": row.enabled,
        "sort_order": row.sort_order,
        "notes": row.notes,
        "args": row.args,
        "needs_date": job.needs_date,
        "output": job.output,
        "s3": s3_target_info(row.template, row.args),
        "s3_folder": s3_folder(row.template, row.args),
        "s3_key_example": s3_key,
        "s3_locked_at": row.s3_locked_at,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
        "can_edit": can_edit(role, row.template),
    }


def _validate_or_422(body: JobDraft, user: dict, db: sqlite3.Connection, original: JobRow | None):
    v = validate_job(body.name, body.template, body.args, role=user["role"], rows=load_job_rows(enabled_only=False),
                     catalog=CATALOG, original=original, confirm_s3_move=body.confirm_s3_move)
    if not v.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"message": "Periksa kembali isian job.", "errors": v.errors,
                             "needs_s3_confirm": v.needs_s3_confirm})
    return v


def _get_or_404(db: sqlite3.Connection, name: str) -> JobRow:
    row = get_job_row(db, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job '{name}' tidak ditemukan.")
    return row


@router.get("")
def list_jobs(user: dict = Depends(current_user)):
    rows = load_job_rows(enabled_only=False)
    example_date = default_target_date()
    return {"example_date": example_date,
            "jobs": [_job_dict(r, j, user["role"]) for r, j in zip(rows, build_jobs(example_date, rows))]}


@router.get("/templates")
def templates(_user: dict = Depends(current_user)):
    """Metadata untuk form: template (label, role minimal, jenis), format tanggal teks, tipe override."""
    return {
        "templates": [{"key": k, **v} for k, v in TEMPLATE_SPECS.items()],
        "strdate_formats": [{"value": k, "label": v} for k, v in STRDATE_FORMATS.items()],
        "override_types": sorted(_SIMPLE_ARROW_TYPES) + ["decimal128(18, 4)", "decimal128(38, 10)", "timestamp[us]"],
    }


@router.post("/validate")
def validate(body: JobDraft, user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    """Validasi draf TANPA menyimpan -- dipanggil form secara live (preview path S3, error per field)."""
    original = _get_or_404(db, body.original_name) if body.original_name else None
    v = validate_job(body.name, body.template, body.args, role=user["role"], rows=load_job_rows(enabled_only=False),
                     catalog=CATALOG, original=original, confirm_s3_move=body.confirm_s3_move)
    return v.as_dict()


@router.post("/test-run")
def test_run_endpoint(body: TestRunIn, user: dict = Depends(require_role("operator")),
                      db: sqlite3.Connection = Depends(get_db)):
    """Uji coba draf (maks `limit` baris, tanpa file/upload, transaksi di-rollback). Divalidasi dulu."""
    original = _get_or_404(db, body.original_name) if body.original_name else None
    target = (body.target_date or date.fromisoformat(default_target_date())).isoformat()
    name = original.name if original else (body.name or "uji_coba")
    v = validate_job(name, body.template, body.args, role=user["role"], rows=load_job_rows(enabled_only=False),
                     catalog=CATALOG, original=original, target_date=target)
    # Uji coba tidak menyimpan apa pun: aturan nama/keunikan & kunci path S3 baru berlaku saat simpan.
    errors = {k: m for k, m in v.errors.items() if k not in ("name", "s3", "output", "dataset")}
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"message": "Perbaiki isian job sebelum uji coba.", "errors": errors})
    job = build_job(name, body.template, body.args, target)
    try:
        result = test_run(job, body.limit)
    except TestRunBusy as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))
    except CatalogError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    except Exception as e:  # noqa: BLE001 -- error SQL / konversi Arrow -> tampilkan ke pengguna
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Uji coba gagal: {e}")
    return {**result, "target_date": target, "query": job.query}


@router.get("/{name}")
def get_job(name: str, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = _get_or_404(db, name)
    job = build_jobs(default_target_date(), [row])[0]
    recent = db.execute("SELECT i.run_id, i.target_date, i.status, i.rows, i.s3_key, i.finished_at, r.created_by "
                        "FROM run_items i JOIN runs r ON r.id = i.run_id WHERE i.job_name = ? "
                        "ORDER BY i.id DESC LIMIT 10", (name,)).fetchall()
    return {**_job_dict(row, job, user["role"]), "query": job.query,
            "history": job_history(db, name), "recent_runs": [dict(r) for r in recent]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: JobCreate, user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    _validate_or_422(body, user, db, None)
    row = JobRow(name=body.name, template=body.template, args=body.args, enabled=body.enabled,
                 sort_order=0, notes=(body.notes or "").strip() or None)
    try:
        create_job(db, row, user["username"])
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Nama '{body.name}' sudah dipakai job lain.")
    return {"name": body.name}


@router.put("/{name}")
def update(name: str, body: JobDraft, user: dict = Depends(require_role("operator")),
           db: sqlite3.Connection = Depends(get_db)):
    old = _get_or_404(db, name)
    body.original_name = name
    v = _validate_or_422(body, user, db, old)
    new = JobRow(name=name, template=body.template, args=body.args, enabled=old.enabled,
                 sort_order=old.sort_order, notes=(body.notes or "").strip() or None)
    update_job(db, old, new, user["username"], s3_moved=v.s3_moved)
    return {"name": name, "s3_moved": v.s3_moved}


@router.post("/{name}/enabled")
def set_enabled(name: str, body: EnabledIn, user: dict = Depends(require_role("operator")),
                db: sqlite3.Connection = Depends(get_db)):
    old = _get_or_404(db, name)
    if not can_edit(user["role"], old.template):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Job ini hanya bisa diubah admin.")
    if old.enabled != body.enabled:
        try:
            build_jobs(default_target_date(), [old])  # definisi rusak tidak boleh diaktifkan
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Job tidak bisa diaktifkan: {e}")
        set_job_enabled(db, old, body.enabled, user["username"])
    return {"name": name, "enabled": body.enabled}
