"""Dependency bersama FastAPI: koneksi database & user yang sedang login."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Iterator

from fastapi import Cookie, Depends, HTTPException, status

from pipeline import run_store
from pipeline.job_store import utc_now

SESSION_COOKIE = "pqx_session"
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def get_db() -> Iterator[sqlite3.Connection]:
    db = run_store.open_db()
    try:
        yield db
    finally:
        db.close()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(pqx_session: str | None = Cookie(default=None),
                 db: sqlite3.Connection = Depends(get_db)) -> dict:
    """User aktif pemilik cookie session. Role dibaca ulang tiap request -> perubahan role/nonaktif langsung berlaku."""
    if pqx_session:
        row = db.execute(
            "SELECT u.username, u.full_name, u.email, u.role, u.can_export FROM sessions s "
            "JOIN users u ON u.username = s.username "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
            (hash_token(pqx_session), utc_now())).fetchone()
        if row:
            return dict(row, can_export=bool(row["can_export"]))
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Belum login atau session sudah habis.")


def can_quick_export(user: dict) -> bool:
    """Ekspor Cepat: operator+ selalu; viewer hanya kalau disetujui admin (akun daftar otomatis SSO = belum)."""
    return ROLE_RANK[user["role"]] >= ROLE_RANK["operator"] or bool(user.get("can_export"))


def require_role(minimum: str):
    def check(user: dict = Depends(current_user)) -> dict:
        if ROLE_RANK[user["role"]] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Butuh role {minimum} atau lebih tinggi.")
        return user
    return check
