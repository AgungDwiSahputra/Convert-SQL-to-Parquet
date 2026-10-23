"""Membuat, memantau, membatalkan & mengulang run. Eksekusinya sendiri dikerjakan worker.py."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from pipeline import run_store
from pipeline.config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BATCH_SIZE, COMPRESSION, S3_BUCKET_NAME
from pipeline.job_store import default_target_date, load_job_rows
from webapp.deps import current_user, get_db, require_role

router = APIRouter()

MAX_RANGE_DAYS = 366
MAX_ITEMS = 5000
PREVIEW_ITEMS = 1000
FINAL_STATUSES = ("success", "failed", "cancelled", "interrupted")


class RunOptionsIn(BaseModel):
    batch: int = Field(BATCH_SIZE, ge=1_000, le=1_000_000)
    compression: Literal["zstd", "snappy", "gzip", "none"] = COMPRESSION
    skip_s3: bool = False
    skip_local: bool = True       # default sama dengan run_exporter.bat (--skip-local)
    stop_on_error: bool = False


class RunIn(BaseModel):
    jobs: list[str] = Field(min_length=1)
    start_date: date
    end_date: date
    options: RunOptionsIn = RunOptionsIn()


def _s3_enabled() -> bool:
    return bool(AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and S3_BUCKET_NAME)


def _plan(body: RunIn, db: sqlite3.Connection) -> tuple[list[run_store.PlannedItem], list[str]]:
    if body.end_date < body.start_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tanggal akhir harus >= tanggal awal.")
    if (body.end_date - body.start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Rentang maksimal {MAX_RANGE_DAYS} hari per run.")
    rows = load_job_rows()  # hanya job aktif
    wanted = set(body.jobs)
    unknown = sorted(wanted - {r.name for r in rows})
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Job tidak dikenal / nonaktif: {', '.join(unknown)}")
    items = run_store.plan_items([r for r in rows if r.name in wanted], body.start_date, body.end_date)
    if len(items) > MAX_ITEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Run ini berisi {len(items):,} eksekusi (maksimal {MAX_ITEMS:,}). Pecah jadi beberapa run.")

    warnings = []
    yesterday = date.fromisoformat(default_target_date())
    if body.end_date > yesterday:
        warnings.append(f"Tanggal setelah {yesterday.isoformat()} (kemarin, WIB): data hari itu mungkin belum lengkap.")
    if not body.options.skip_s3 and not _s3_enabled():
        warnings.append("Kredensial S3 di server belum lengkap — upload S3 akan dilewati.")
    if not run_store.worker_online(db):
        warnings.append("Worker tidak aktif — run akan menunggu di antrian sampai worker.py dijalankan.")
    return items, warnings


@router.get("/meta")
def meta(_user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return {
        "default_date": default_target_date(),
        "default_options": RunOptionsIn().model_dump(),
        "compression_options": ["zstd", "snappy", "gzip", "none"],
        "s3_enabled": _s3_enabled(),
        "s3_bucket": S3_BUCKET_NAME,
        "worker_online": run_store.worker_online(db),
        "max_range_days": MAX_RANGE_DAYS,
    }


@router.post("/preview")
def preview(body: RunIn, _user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    items, warnings = _plan(body, db)
    upload = not body.options.skip_s3 and _s3_enabled()
    return {
        "total_items": len(items),
        "job_count": len({i.job_name for i in items}),
        "date_count": (body.end_date - body.start_date).days + 1,
        "s3_upload": upload,
        "s3_bucket": S3_BUCKET_NAME if upload else None,
        "warnings": warnings,
        "items": [vars(i) | {"args": None} for i in items[:PREVIEW_ITEMS]],
        "truncated": len(items) > PREVIEW_ITEMS,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_run(body: RunIn, user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    items, _warnings = _plan(body, db)
    run_id = run_store.create_run(db, user["username"], body.start_date, body.end_date,
                                  body.options.model_dump(), items)
    return {"id": run_id}


@router.get("")
def list_runs(limit: int = Query(50, ge=1, le=200), before_id: int | None = None,
              _user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return {"runs": run_store.list_runs(db, limit, before_id)}


def _get_run_or_404(db: sqlite3.Connection, run_id: int) -> dict:
    run = run_store.get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run #{run_id} tidak ditemukan.")
    return run


@router.get("/{run_id}")
def get_run(run_id: int, _user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    return _get_run_or_404(db, run_id)


@router.get("/{run_id}/log")
def get_log(run_id: int, offset: int = Query(0, ge=0), _user: dict = Depends(current_user),
            db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run #{run_id} tidak ditemukan.")
    text, next_offset = run_store.read_run_log(run_id, offset)
    return {"text": text, "offset": next_offset, "finished": row["status"] in FINAL_STATUSES}


@router.post("/{run_id}/cancel")
def cancel(run_id: int, _user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    try:
        return {"status": run_store.request_cancel(db, run_id)}
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run #{run_id} tidak ditemukan.")


@router.post("/{run_id}/retry", status_code=status.HTTP_201_CREATED)
def retry(run_id: int, user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    run = _get_run_or_404(db, run_id)
    if run["status"] not in FINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Run masih berjalan / mengantri.")
    try:
        items = run_store.retry_plan(db, run_id, load_job_rows())
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Tidak bisa mengulang: {e}")
    if not items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Semua item run ini sudah berhasil.")
    new_id = run_store.create_run(db, user["username"], date.fromisoformat(run["start_date"]),
                                  date.fromisoformat(run["end_date"]), run["options"], items, retry_of=run_id)
    return {"id": new_id}
