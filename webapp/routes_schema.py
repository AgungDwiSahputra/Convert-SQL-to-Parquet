"""Penjelajah skema SQL Server untuk wizard tambah job (operator+). Hanya membaca metadata."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from pipeline.job_validation import DATETIME_TYPES, EPOCH_TYPES, STRING_TYPES
from webapp.catalog import CATALOG, CatalogError, detect_date_format
from webapp.deps import require_role

router = APIRouter()


def _date_kind(data_type: str) -> str | None:
    if data_type in DATETIME_TYPES:
        return "datetime"
    if data_type in STRING_TYPES:
        return "string"
    if data_type in EPOCH_TYPES:
        return "epoch"
    return None


def _call(fn, *args):
    try:
        return fn(*args)
    except CatalogError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/databases")
def databases(_user: dict = Depends(require_role("operator"))):
    return {"databases": _call(CATALOG.databases)}


@router.get("/tables")
def tables(db: str = Query(...), _user: dict = Depends(require_role("operator"))):
    data = _call(CATALOG.tables, db)
    return {"tables": [{"name": n, **info} for n, info in data.items()]}


@router.get("/columns")
def columns(db: str = Query(...), table: str = Query(...), _user: dict = Depends(require_role("operator"))):
    data = _call(CATALOG.columns, db, table)
    return {"rows": _call(CATALOG.table_rows, db, table),
            "columns": [{"name": n, **info, "date_kind": _date_kind(info["type"])} for n, info in data.items()]}


@router.get("/sample")
def sample(db: str = Query(...), table: str = Query(...), column: str = Query(...),
           _user: dict = Depends(require_role("operator"))):
    """Contoh nilai kolom tanggal + tebakan template/format yang tepat (cegah job yang selalu 0 baris)."""
    cols = _call(CATALOG.columns, db, table)
    info = next((v for k, v in cols.items() if k.lower() == column.lower()), None)
    if info is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Kolom '{column}' tidak ada.")
    values = _call(CATALOG.sample_values, db, table, column)
    detected = detect_date_format(values, info["type"]) if _date_kind(info["type"]) in ("string", "epoch") else \
        {"ok": True, "template": "bronze_daily", "date_format": None, "message": f"Tipe {info['type']} — filter rentang per hari."}
    return {"values": [str(v) for v in values], "detected": detected}
