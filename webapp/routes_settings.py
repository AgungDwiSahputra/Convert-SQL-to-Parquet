"""
Pengaturan aplikasi (khusus admin): penerima & saklar notifikasi email, email uji, riwayat
notifikasi, status pemeliharaan. Akun SMTP TIDAK bisa dilihat/diubah di sini -- hanya di .env server.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import maintenance
import notify
from app_settings import get_settings, set_settings
from config import APP_BASE_URL, BACKUP_KEEP, LOG_RETENTION_DAYS, SMTP_SECURITY
from webapp.deps import get_db, require_role

router = APIRouter()


class NotifyIn(BaseModel):
    recipients: list[str] = Field(default_factory=list, max_length=50)
    on_failed: bool = True
    on_anomaly: bool = True
    notify_creator: bool = True
    anomaly_streak: int = Field(3, ge=2, le=14)


class TestEmailIn(BaseModel):
    to: str | None = None


@router.get("")
def get_all(_admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    s = get_settings(db, "notify.")
    rows = db.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 30").fetchall()
    return {
        "notify": {"recipients": s["notify.recipients"], "on_failed": s["notify.on_failed"],
                   "on_anomaly": s["notify.on_anomaly"], "notify_creator": s["notify.notify_creator"],
                   "anomaly_streak": s["notify.anomaly_streak"]},
        # Hanya status -- alamat server/akun SMTP sengaja tidak ditampilkan.
        "smtp": {"configured": notify.smtp_configured(), "security": SMTP_SECURITY,
                 "from_matches_login": notify.from_matches_login()},
        "app_base_url": APP_BASE_URL,
        "maintenance": {"last": maintenance.last_result(db), "backup_keep": BACKUP_KEEP,
                        "log_retention_days": LOG_RETENTION_DAYS},
        "notifications": [dict(r, recipients=json.loads(r["recipients"]),
                               job_names=json.loads(r["job_names"]) if r["job_names"] else None) for r in rows],
    }


@router.put("/notify")
def save_notify(body: NotifyIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    recipients = sorted({r.strip().lower() for r in body.recipients if r.strip()})
    bad = [r for r in recipients if not notify.EMAIL_RE.match(r)]
    if bad:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Alamat email tidak valid: {', '.join(bad)}")
    set_settings(db, {"notify.recipients": recipients, "notify.on_failed": body.on_failed,
                      "notify.on_anomaly": body.on_anomaly, "notify.notify_creator": body.notify_creator,
                      "notify.anomaly_streak": body.anomaly_streak}, admin["username"])
    return {"ok": True, "recipients": recipients}


@router.post("/test-email")
def test_email(body: TestEmailIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    to = (body.to or admin.get("email") or "").strip()
    if not notify.EMAIL_RE.match(to):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Isi alamat email tujuan yang valid.")
    st, error = notify.send_test(db, to, admin["username"])
    return {"status": st, "error": error, "to": to}
