"""
LOGIN via SSO portal.

  1. Browser mengirim username/password ke /api/auth/login (aplikasi ini).
  2. Server meneruskannya ke SSO {SSO_BASE_URL}/api/auth/gettoken. Password TIDAK disimpan / di-log.
  3. user_name hasil SSO harus TERDAFTAR & AKTIF di tabel users (role diatur admin aplikasi).
  4. Nama lengkap & email diambil dari SSO /api/cor/user -- hanya dua field itu yang disimpan;
     data pribadi lain di response (NIK, No. KTP, dst.) diabaikan.
  5. Aplikasi membuat session SENDIRI (cookie HttpOnly). Token SSO hanya dipakai sesaat di
     langkah 2-4 lalu dibuang -- tiap request divalidasi lewat session aplikasi, bukan token SSO
     (tidak bergantung umur token SSO 1 jam, dan tidak membebani SSO tiap klik).
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from pipeline.config import COOKIE_SECURE, SESSION_HOURS, SSO_BASE_URL
from pipeline.job_store import utc_now
from webapp.deps import SESSION_COOKIE, current_user, get_db, hash_token

router = APIRouter()

# Satu client dipakai ulang (koneksi & SSL context tidak dibuat ulang tiap login). Thread-safe.
_sso = httpx.Client(timeout=15)

# Batas percobaan login gagal per (IP, username) -- di memori proses, cukup untuk tool internal.
_MAX_FAILED, _FAILED_WINDOW_S = 5, 600
_failed: dict[str, list[float]] = {}


def _too_many_failures(key: str) -> bool:
    now = time.time()
    _failed[key] = [t for t in _failed.get(key, []) if now - t < _FAILED_WINDOW_S]
    return len(_failed[key]) >= _MAX_FAILED


def _sso_get_token(username: str, password: str) -> dict | None:
    """Response gettoken kalau login SSO berhasil, None kalau username/password ditolak."""
    if not SSO_BASE_URL:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "SSO_BASE_URL belum diisi di .env server.")
    try:
        r = _sso.post(f"{SSO_BASE_URL}/api/auth/gettoken", json={"username": username, "password": password})
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Server SSO tidak bisa dihubungi. Coba lagi nanti.")
    if r.status_code >= 500:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Server SSO error (HTTP {r.status_code}).")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("status") or not body.get("access_token"):
        return None
    return body


def _sso_profile(access_token: str, user_id) -> dict:
    """Profil user dari SSO; {} kalau gagal (login tetap lanjut dengan data dari gettoken)."""
    try:
        r = _sso.post(f"{SSO_BASE_URL}/api/cor/user", json={"userid": user_id},
                      headers={"Authorization": f"Bearer {access_token}"})
        data = r.json().get("data") if r.status_code == 200 else None
        return data[0] if data else {}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError, IndexError):
        return {}


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: sqlite3.Connection = Depends(get_db)):
    typed = body.username.strip()
    key = f"{request.client.host if request.client else '?'}|{typed.lower()}"
    if _too_many_failures(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Terlalu banyak percobaan login gagal. Coba lagi dalam 10 menit.")

    sso = _sso_get_token(typed, body.password)
    if sso is None:
        _failed.setdefault(key, []).append(time.time())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Username atau password salah.")
    _failed.pop(key, None)

    username = str(sso.get("user_name") or typed).strip().lower()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None or not user["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Akun '{username}' belum terdaftar di aplikasi ini. Minta admin untuk menambahkan.")

    profile = _sso_profile(sso["access_token"], sso.get("user_id"))
    full_name = profile.get("UserFullName") or profile.get("Nama") or user["full_name"]
    email = profile.get("UserEmail") or sso.get("user_email") or user["email"]

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))
    db.execute("INSERT INTO sessions (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
               (hash_token(token), username, now.isoformat(timespec="seconds"),
                (now + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")))
    db.execute("UPDATE users SET full_name = ?, email = ?, last_login_at = ? WHERE username = ?",
               (full_name, email, utc_now(), username))
    response.set_cookie(SESSION_COOKIE, token, max_age=int(SESSION_HOURS * 3600), httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")
    return {"username": username, "full_name": full_name, "email": email, "role": user["role"]}


@router.post("/logout")
def logout(response: Response, pqx_session: str | None = Cookie(default=None),
           db: sqlite3.Connection = Depends(get_db)):
    if pqx_session:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(pqx_session),))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user
