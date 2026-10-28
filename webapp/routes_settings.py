"""
Pengaturan aplikasi (khusus admin): penerima & saklar notifikasi email, email uji, riwayat
notifikasi, status pemeliharaan, data quality & inventaris S3, pendaftaran otomatis akun SSO. Akun SMTP TIDAK bisa dilihat/diubah di sini -- hanya di .env server.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from pipeline import maintenance, notify, s3_inventory
from pipeline.app_settings import get_settings, set_settings
from pipeline.config import APP_BASE_URL, BACKUP_KEEP, LOG_RETENTION_DAYS, SMTP_SECURITY
from pipeline.export import make_s3_client
from webapp.deps import get_db, require_role

router = APIRouter()


class NotifyIn(BaseModel):
    recipients: list[str] = Field(default_factory=list, max_length=50)
    on_failed: bool = True
    on_anomaly: bool = True
    on_dq_warning: bool = True
    notify_creator: bool = True
    anomaly_streak: int = Field(3, ge=2, le=14)


class DqIn(BaseModel):
    volume_low_pct: int = Field(50, ge=1, le=99)
    volume_high_pct: int = Field(200, ge=101, le=1000)
    volume_min_history: int = Field(7, ge=3, le=60)
    gap_days: int = Field(90, ge=7, le=366)
    gap_weeks: int = Field(26, ge=4, le=104)
    gap_months: int = Field(12, ge=2, le=36)
    freshness_enabled: bool = False
    freshness_check_time: str = Field("06:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    s3inv_enabled: bool = True
    s3inv_scan_time: str = Field("05:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class AuthIn(BaseModel):
    auto_viewer: bool = True


class TestEmailIn(BaseModel):
    to: str | None = None


@router.get("")
def get_all(_admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    s = get_settings(db, "notify.")
    d = get_settings(db, "dq.") | get_settings(db, "freshness.") | get_settings(db, "s3inv.")
    rows = db.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 30").fetchall()
    return {
        "notify": {"recipients": s["notify.recipients"], "on_failed": s["notify.on_failed"],
                   "on_anomaly": s["notify.on_anomaly"], "on_dq_warning": s["notify.on_dq_warning"],
                   "notify_creator": s["notify.notify_creator"],
                   "anomaly_streak": s["notify.anomaly_streak"]},
        "dq": {"volume_low_pct": d["dq.volume_low_pct"], "volume_high_pct": d["dq.volume_high_pct"],
               "volume_min_history": d["dq.volume_min_history"], "gap_days": d["dq.gap_days"],
               "gap_weeks": d["dq.gap_weeks"], "gap_months": d["dq.gap_months"],
               "freshness_enabled": d["freshness.enabled"],
               "freshness_check_time": d["freshness.check_time"], "freshness_last": d["freshness.last_result"],
               "s3inv_enabled": d["s3inv.enabled"], "s3inv_scan_time": d["s3inv.scan_time"],
               "s3inv_last": d["s3inv.last_result"]},
        # Hanya status -- alamat server/akun SMTP sengaja tidak ditampilkan.
        "smtp": {"configured": notify.smtp_configured(), "security": SMTP_SECURITY,
                 "from_matches_login": notify.from_matches_login(), "from_name": notify.from_name()},
        "auth": {"auto_viewer": get_settings(db, "auth.")["auth.auto_viewer"],
                 "pending_export": db.execute("SELECT COUNT(*) FROM users WHERE role = 'viewer' AND active = 1 "
                                              "AND can_export = 0").fetchone()[0]},
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
                      "notify.on_anomaly": body.on_anomaly, "notify.on_dq_warning": body.on_dq_warning,
                      "notify.notify_creator": body.notify_creator,
                      "notify.anomaly_streak": body.anomaly_streak}, admin["username"])
    return {"ok": True, "recipients": recipients}


@router.put("/dq")
def save_dq(body: DqIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    set_settings(db, {"dq.volume_low_pct": body.volume_low_pct, "dq.volume_high_pct": body.volume_high_pct,
                      "dq.volume_min_history": body.volume_min_history, "dq.gap_days": body.gap_days,
                      "dq.gap_weeks": body.gap_weeks, "dq.gap_months": body.gap_months,
                      "freshness.enabled": body.freshness_enabled,
                      "freshness.check_time": body.freshness_check_time, "s3inv.enabled": body.s3inv_enabled,
                      "s3inv.scan_time": body.s3inv_scan_time}, admin["username"])
    return {"ok": True}


@router.put("/auth")
def save_auth(body: AuthIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    set_settings(db, {"auth.auto_viewer": body.auto_viewer}, admin["username"])
    return {"ok": True}


@router.post("/s3-inventory/scan")
def scan_s3_inventory(admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    """Pindai inventaris S3 sekarang (hanya membaca S3; beberapa detik untuk puluhan job)."""
    return s3_inventory.scan(db, make_s3_client(False), by=admin["username"])


@router.post("/test-email")
def test_email(body: TestEmailIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    to = (body.to or admin.get("email") or "").strip()
    if not notify.EMAIL_RE.match(to):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Isi alamat email tujuan yang valid.")
    st, error = notify.send_test(db, to, admin["username"])
    return {"status": st, "error": error, "to": to}
