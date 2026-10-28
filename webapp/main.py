"""
WEB UI -- FastAPI: API di /api/... + file hasil build frontend React (frontend/dist).

    python -m uvicorn webapp.main:app --host 0.0.0.0 --port 8000     (jalankan dari folder proyek)

Eksekusi run TIDAK dilakukan di sini, tapi oleh worker.py (proses terpisah) -- web hanya
mengantrikan run dan menampilkan status/log-nya. Dokumentasi API: {BASE_PATH}/api/docs

BASE_PATH (.env, mis. /apps/monitoring-datas): semua route ada di bawah prefix itu, karena gateway
meneruskan path utuh. Frontend di-build dengan base relatif; prefix-nya disuntikkan saat index.html
disajikan (<base href>), jadi satu hasil build bisa dipakai di root maupun sub-path mana pun.
"""

from __future__ import annotations

import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from pipeline import run_store
from pipeline.config import BASE_PATH
from pipeline.job_store import init_db
from webapp import (
    routes_adhoc, routes_auth, routes_dashboard, routes_jobs, routes_runs, routes_schedules, routes_schema, routes_settings,
    routes_users,
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Di Windows, mimetypes membaca registry -- .js sering terdaftar sebagai text/plain, dan browser
# MENOLAK menjalankan <script type="module"> dengan MIME itu (halaman putih). Kunci eksplisit.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db = run_store.open_db()  # error jelas kalau jobs.db belum ada (jalankan migrate_jobs.py dulu)
    try:
        init_db(db)           # naikkan skema ke versi terbaru (mis. v1 Fase 0 -> v2 Fase 1)
    finally:
        db.close()
    yield


API = f"{BASE_PATH}/api"

app = FastAPI(title="Parquet Exporter", lifespan=lifespan,
              docs_url=f"{API}/docs", redoc_url=None, openapi_url=f"{API}/openapi.json")

app.include_router(routes_auth.router, prefix=f"{API}/auth", tags=["auth"])
app.include_router(routes_jobs.router, prefix=f"{API}/jobs", tags=["jobs"])
app.include_router(routes_runs.router, prefix=f"{API}/runs", tags=["runs"])
app.include_router(routes_users.router, prefix=f"{API}/users", tags=["users"])
app.include_router(routes_schema.router, prefix=f"{API}/schema", tags=["schema"])
app.include_router(routes_schedules.router, prefix=f"{API}/schedules", tags=["schedules"])
app.include_router(routes_adhoc.router, prefix=f"{API}/adhoc", tags=["adhoc"])
app.include_router(routes_settings.router, prefix=f"{API}/settings", tags=["settings"])
app.include_router(routes_dashboard.router, prefix=f"{API}/dashboard", tags=["dashboard"])


@app.get(f"{API}/health", tags=["health"])
def health():
    """Untuk monitoring (tanpa login): database bisa dibuka & apakah worker melapor baru-baru ini."""
    try:
        db = run_store.open_db()
        try:
            worker = run_store.worker_online(db)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"database: {e}"}, status_code=503)
    return {"ok": True, "worker_online": worker}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith(f"{API}/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- Frontend (Single Page App) ---------------------------------------------
if (FRONTEND_DIST / "assets").is_dir():
    app.mount(f"{BASE_PATH}/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

if BASE_PATH:
    @app.get("/", include_in_schema=False)
    @app.get(BASE_PATH, include_in_schema=False)
    def to_base():
        """Akses langsung ke root port (atau prefix tanpa '/') -> halaman Web UI di bawah BASE_PATH."""
        return RedirectResponse(f"{BASE_PATH}/")


def _index_html(index: Path) -> HTMLResponse:
    """index.html dengan <base href> = BASE_PATH/: asset (base relatif dari Vite), React Router, dan
    panggilan API di frontend semuanya membaca prefix dari tag ini."""
    html = re.sub(r'<base href="[^"]*"', f'<base href="{BASE_PATH}/"', index.read_text(encoding="utf-8"), count=1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get(f"{BASE_PATH}/{{path:path}}", include_in_schema=False)
def spa(path: str):
    """File statis hasil build kalau ada; selain itu index.html (routing diurus React Router)."""
    if path.startswith("api/"):
        raise HTTPException(404, "Endpoint API tidak ditemukan.")
    root = FRONTEND_DIST.resolve()
    candidate = (root / path).resolve()
    if path and path != "index.html" and candidate.is_file() and root in candidate.parents:
        return FileResponse(candidate)
    index = root / "index.html"
    if index.is_file():
        return _index_html(index)
    return PlainTextResponse("Frontend belum di-build. Jalankan: cd frontend && npm install && npm run build",
                             status_code=503)
