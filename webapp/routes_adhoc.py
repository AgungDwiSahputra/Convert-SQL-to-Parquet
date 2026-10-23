"""
EKSPOR CEPAT (ad-hoc): download .parquet langsung dari browser, TANPA membuat job dan TANPA S3.

  /job   (viewer+)    : data job yang SUDAH terdaftar & aktif, untuk satu tanggal. Data yang bisa
                         diunduh = data yang memang sudah diekspor pipeline -- tabel sensitif yang
                         tidak dijadikan job tidak bisa diambil Viewer.
  /table (operator+)  : tabel bebas (opsional difilter kolom tanggal datetime). Divalidasi aturan
                         yang sama dengan wizard job (identifier dicek ke SQL Server).
Job bertemplate khusus admin (mis. sp_job = menjalankan ETL Load) hanya bisa diunduh admin.
Batas ADHOC_MAX_ROWS baris; setiap download tercatat di tabel adhoc_exports.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from pipeline.config import ADHOC_MAX_ROWS
from pipeline.job_store import build_jobs, default_target_date, get_job_row, load_job_rows, utc_now
from pipeline.job_templates import build_job
from pipeline.job_validation import ROLE_RANK, TEMPLATE_SPECS, validate_job
from webapp.catalog import CATALOG, CatalogError, TestRunBusy, export_parquet
from webapp.deps import current_user, get_db, require_role

router = APIRouter()


def _downloadable(role: str, template: str) -> bool:
    """Template biasa (bronze_*) boleh diunduh siapa saja yang login; template khusus admin hanya admin."""
    return TEMPLATE_SPECS.get(template, {}).get("role") == "operator" or ROLE_RANK.get(role, 0) >= ROLE_RANK["admin"]


class JobExportIn(BaseModel):
    job_name: str
    target_date: date | None = None


class TableExportIn(BaseModel):
    db: str
    table: str
    date_col: str | None = None
    target_date: date | None = None


def _log(db, user, mode, source, target_date, result=None, error=None, size=None):
    db.execute("INSERT INTO adhoc_exports (username, mode, source, target_date, rows, truncated, size_bytes, status, "
               "error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (user["username"], mode, source, target_date, (result or {}).get("rows"),
                int(bool((result or {}).get("truncated"))), size, "failed" if error else "success", error, utc_now()))


def _run_export(db, user, job, mode, source, target_date, filename) -> FileResponse:
    fd, path = tempfile.mkstemp(prefix="adhoc_", suffix=".parquet")
    os.close(fd)
    try:
        result = export_parquet(job, path, ADHOC_MAX_ROWS)
    except TestRunBusy as e:
        os.remove(path)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))
    except Exception as e:  # noqa: BLE001 -- error SQL/konversi -> tampilkan & catat
        os.remove(path)
        _log(db, user, mode, source, target_date, error=str(e)[:500])
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ekspor gagal: {e}")
    size = os.path.getsize(path)
    _log(db, user, mode, source, target_date, result, size=size)
    return FileResponse(path, media_type="application/vnd.apache.parquet", filename=filename,
                        background=BackgroundTask(os.remove, path),
                        headers={"X-Rows": str(result["rows"]), "X-Truncated": "1" if result["truncated"] else "0",
                                 "X-Max-Rows": str(ADHOC_MAX_ROWS), "X-Elapsed": str(result["elapsed_s"])})


@router.get("/meta")
def meta(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    rows = load_job_rows()
    example = default_target_date()
    jobs = []
    for r, j in zip(rows, build_jobs(example, rows)):
        if _downloadable(user["role"], r.template):
            jobs.append({"name": r.name, "needs_date": j.needs_date, "template": r.template,
                         "table": r.args.get("table"), "db": r.args.get("db")})
    mine = db.execute("SELECT * FROM adhoc_exports WHERE username = ? ORDER BY id DESC LIMIT 15",
                      (user["username"],)).fetchall()
    return {"max_rows": ADHOC_MAX_ROWS, "default_date": example, "jobs": jobs, "history": [dict(m) for m in mine]}


@router.post("/job")
def export_job(body: JobExportIn, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = get_job_row(db, body.job_name)
    if row is None or not row.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job aktif '{body.job_name}' tidak ditemukan.")
    if not _downloadable(user["role"], row.template):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Job ini memakai template khusus admin.")
    target = (body.target_date or date.fromisoformat(default_target_date())).isoformat()
    job = build_job(row.name, row.template, row.args, target)
    suffix = f"_{target.replace('-', '')}" if job.needs_date else f"_snapshot_{target.replace('-', '')}"
    return _run_export(db, user, job, "job", row.name, target if job.needs_date else None, f"{row.name}{suffix}.parquet")


@router.post("/table")
def export_table(body: TableExportIn, user: dict = Depends(require_role("operator")),
                 db: sqlite3.Connection = Depends(get_db)):
    target = (body.target_date or date.fromisoformat(default_target_date())).isoformat()
    args = {"db": body.db, "table": body.table, "dataset": "adhoc", "module": None, "output": "adhoc.parquet"}
    template = "bronze_master"
    if body.date_col:
        template, args = "bronze_daily", {**args, "date_col": body.date_col}
    v = validate_job("adhoc_export", template, args, role=user["role"], rows=[], catalog=CATALOG, target_date=target)
    errors = {k: m for k, m in v.errors.items() if k not in ("name", "s3", "output", "dataset")}
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(f"{k}: {m}" for k, m in errors.items()))
    job = build_job("adhoc_export", template, args, target)
    safe = re.sub(r"[^A-Za-z0-9_]", "_", body.table)
    filename = f"{safe}_{target.replace('-', '')}.parquet" if body.date_col else f"{safe}_full.parquet"
    try:
        return _run_export(db, user, job, "table", f"{body.db}.dbo.{body.table}", target if body.date_col else None,
                           filename)
    except CatalogError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
