"""Jadwal run otomatis (lihat: semua user; ubah: operator+). Eksekusi jadwal dilakukan worker.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

import scheduler
from job_store import load_job_rows
from webapp.deps import current_user, get_db, require_role
from webapp.routes_runs import RunOptionsIn

router = APIRouter()


class SelectorIn(BaseModel):
    mode: Literal["pattern", "modules", "jobs"]
    patterns: list[str] = []
    modules: list[str] = []
    jobs: list[str] = []


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    frequency: Literal["daily", "weekly"]
    time_of_day: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekdays: list[int] = []
    date_offset: int = Field(1, ge=0, le=30)
    selector: SelectorIn
    options: RunOptionsIn = RunOptionsIn()

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v):
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("hari 0 (Senin) s/d 6 (Minggu)")
        return sorted(set(v))


def _validate(body: ScheduleIn) -> dict:
    if body.frequency == "weekly" and not body.weekdays:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pilih minimal satu hari untuk jadwal mingguan.")
    sel = body.selector
    if not {"pattern": sel.patterns, "modules": sel.modules, "jobs": sel.jobs}[sel.mode]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cakupan job masih kosong.")
    data = body.model_dump()
    data["name"] = body.name.strip()
    data["weekdays"] = body.weekdays if body.frequency == "weekly" else []
    return data


def _enrich(s: dict, rows) -> dict:
    jobs = scheduler.resolve_selector(s["selector"], rows)
    return {**s, "description": scheduler.describe(s), "job_count": len(jobs),
            "job_names": [j.name for j in jobs[:50]]}


@router.get("")
def list_schedules(_user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    rows = load_job_rows()
    return {"schedules": [_enrich(s, rows) for s in scheduler.list_schedules(db)]}


@router.post("/preview")
def preview(selector: SelectorIn, _user: dict = Depends(current_user)):
    jobs = scheduler.resolve_selector(selector.model_dump(), load_job_rows())
    return {"count": len(jobs), "jobs": [j.name for j in jobs[:100]]}


def _get_or_404(db, sid: int) -> dict:
    s = scheduler.get_schedule(db, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jadwal tidak ditemukan.")
    return s


def _save(db, body: ScheduleIn, user: dict, sid: int | None) -> int:
    try:
        return scheduler.save_schedule(db, _validate(body), user["username"], sid)
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Nama jadwal '{body.name}' sudah dipakai.")


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: ScheduleIn, user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    return {"id": _save(db, body, user, None)}


@router.put("/{sid}")
def update(sid: int, body: ScheduleIn, user: dict = Depends(require_role("operator")),
           db: sqlite3.Connection = Depends(get_db)):
    _get_or_404(db, sid)
    return {"id": _save(db, body, user, sid)}


class EnabledIn(BaseModel):
    enabled: bool


@router.post("/{sid}/enabled")
def set_enabled(sid: int, body: EnabledIn, user: dict = Depends(require_role("operator")),
                db: sqlite3.Connection = Depends(get_db)):
    _get_or_404(db, sid)
    scheduler.set_enabled(db, sid, body.enabled, user["username"])
    return scheduler.get_schedule(db, sid)


@router.delete("/{sid}")
def delete(sid: int, _user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    _get_or_404(db, sid)
    db.execute("UPDATE runs SET schedule_id = NULL WHERE schedule_id = ?", (sid,))
    db.execute("DELETE FROM schedules WHERE id = ?", (sid,))
    return {"ok": True}


@router.post("/{sid}/run-now", status_code=status.HTTP_201_CREATED)
def run_now(sid: int, _user: dict = Depends(require_role("operator")), db: sqlite3.Connection = Depends(get_db)):
    """Buat run sekarang dengan cakupan & opsi jadwal ini (tanggal data = hari ini - offset). Jadwal tidak bergeser."""
    s = _get_or_404(db, sid)
    run_id = scheduler.fire(db, s, datetime.now(timezone.utc), " (dijalankan manual)")
    if run_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada job aktif yang cocok dengan cakupan jadwal ini.")
    return {"id": run_id}
