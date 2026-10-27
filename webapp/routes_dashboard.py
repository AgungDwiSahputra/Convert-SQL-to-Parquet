"""
Ringkasan untuk halaman Beranda (viewer+): data quality 7 hari & data ter-load per tanggal 14 hari.

Sumbernya dq_results (CLI & worker). Per (job, tanggal data) dipakai hasil TERBARU, jadi masalah yang
sudah beres lewat run ulang tidak dihitung lagi. Kesegaran & celah per job ada di /api/jobs.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from pipeline.job_store import default_target_date
from webapp.deps import current_user, get_db

router = APIRouter()

# Hasil terbaru per (job, tanggal data) -- di seluruh riwayat, supaya run ulang yang sukses "menutup" masalah lama.
_LATEST = """
SELECT d.* FROM dq_results d
WHERE d.id = (SELECT MAX(x.id) FROM dq_results x WHERE x.job_name = d.job_name AND x.target_date = d.target_date)
"""


def _messages(checks: str | None, status: str) -> str:
    try:
        return "; ".join(f"{c['name']}: {c['message']}" for c in json.loads(checks or "[]") if c["status"] == status)
    except (ValueError, KeyError, TypeError):
        return ""


@router.get("")
def summary(_user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    data_date = date.fromisoformat(default_target_date())
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    recent = db.execute(_LATEST + " AND d.created_at >= ? ORDER BY d.target_date DESC, d.job_name", (since,)).fetchall()
    counts = {"ok": 0, "warning": 0, "blocked": 0, "unchecked": 0}
    issues = {"blocked": [], "warning": []}
    for r in recent:
        counts[r["dq_status"]] = counts.get(r["dq_status"], 0) + 1
        if r["dq_status"] in issues:
            issues[r["dq_status"]].append({"job_name": r["job_name"], "target_date": r["target_date"],
                                           "run_id": r["run_id"], "message": _messages(r["checks"], r["dq_status"])})

    first = data_date - timedelta(days=13)
    by_day = {(first + timedelta(days=i)).isoformat(): {"rows": 0, "jobs": 0, "blocked": 0} for i in range(14)}
    for r in db.execute(_LATEST + " AND d.target_date BETWEEN ? AND ?", (first.isoformat(), data_date.isoformat())):
        day = by_day[r["target_date"]]
        if r["dq_status"] == "blocked":
            day["blocked"] += 1
        elif r["published"] or not r["rows"]:
            day["rows"] += r["rows"] or 0
            day["jobs"] += 1
    return {
        "data_date": data_date.isoformat(),
        "dq7": {**counts, "total": sum(counts.values())},
        "blocked": issues["blocked"][:20],
        "warnings": issues["warning"][:20],
        "daily": [{"date": d, **v} for d, v in by_day.items()],
    }
