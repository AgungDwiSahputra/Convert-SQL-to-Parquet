"""
Kelola user Web UI (khusus admin). Login tetap lewat SSO -- di sini hanya siapa boleh masuk, role-nya, dan
persetujuan Ekspor Cepat untuk Viewer (can_export; akun daftar otomatis lewat SSO mulai dari 0).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from pipeline.job_store import utc_now
from webapp.deps import get_db, require_role

router = APIRouter()

USERNAME_RE = re.compile(r"^[a-z0-9._-]{2,64}$")
Role = Literal["viewer", "operator", "admin"]


class UserIn(BaseModel):
    username: str
    role: Role


class UserPatch(BaseModel):
    role: Role | None = None
    active: bool | None = None
    can_export: bool | None = None


def _normalize(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Username SSO hanya boleh huruf kecil, angka, titik, minus, underscore (2-64 karakter).")
    return username


@router.get("")
def list_users(_admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT username, full_name, email, role, active, can_export, created_at, created_by, "
                      "last_login_at FROM users ORDER BY active DESC, username").fetchall()
    return {"users": [dict(r, active=bool(r["active"]), can_export=bool(r["can_export"])) for r in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
def add_user(body: UserIn, admin: dict = Depends(require_role("admin")), db: sqlite3.Connection = Depends(get_db)):
    username = _normalize(body.username)
    try:
        db.execute("INSERT INTO users (username, role, active, can_export, created_at, created_by) "
                   "VALUES (?, ?, 1, 1, ?, ?)",
                   (username, body.role, utc_now(), admin["username"]))
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"User '{username}' sudah terdaftar.")
    return {"username": username, "role": body.role}


@router.patch("/{username}")
def update_user(username: str, body: UserPatch, _admin: dict = Depends(require_role("admin")),
                db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT role, active, can_export FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' tidak ditemukan.")
    role = body.role or user["role"]
    active = user["active"] if body.active is None else int(body.active)
    # Jangan sampai aplikasi tanpa admin aktif sama sekali (terkunci, harus lewat manage.py).
    if user["role"] == "admin" and user["active"] and (role != "admin" or not active):
        others = db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND username != ?",
                            (username,)).fetchone()[0]
        if others == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minimal harus ada satu admin aktif.")
    can_export = user["can_export"] if body.can_export is None else int(body.can_export)
    db.execute("UPDATE users SET role = ?, active = ?, can_export = ? WHERE username = ?",
               (role, active, can_export, username))
    if not active:
        db.execute("DELETE FROM sessions WHERE username = ?", (username,))
    return {"username": username, "role": role, "active": bool(active), "can_export": bool(can_export)}
