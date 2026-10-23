@echo off
REM Web UI: API FastAPI + frontend React hasil build (frontend\dist).
REM Port/host bisa diubah lewat environment variable WEB_HOST / WEB_PORT sebelum menjalankan.
REM Eksekusi run TIDAK di sini -- jalankan juga run_worker.bat (proses terpisah).
cd /d "%~dp0"
call scripts\find_python.bat
if errorlevel 1 exit /b %errorlevel%

if "%WEB_HOST%"=="" set "WEB_HOST=0.0.0.0"
if "%WEB_PORT%"=="" set "WEB_PORT=8000"

if not exist "frontend\dist\index.html" (
    echo PERINGATAN: frontend\dist belum ada -- jalankan dulu: cd frontend ^&^& npm install ^&^& npm run build
)

"%PYTHON_EXE%" -m uvicorn webapp.main:app --host %WEB_HOST% --port %WEB_PORT%
exit /b %ERRORLEVEL%
