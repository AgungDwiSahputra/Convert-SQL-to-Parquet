@echo off
REM Helper dipanggil run_web.bat / run_worker.bat: isi PYTHON_EXE dengan python yang ada di PATH
REM ("python", lalu Python Launcher "py") -- pola sama dengan run_exporter.bat, tanpa hardcode path.
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_EXE=python"
    exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_EXE=py"
    exit /b 0
)
echo ERROR: python.exe / py.exe tidak ditemukan di PATH komputer ini.
exit /b 9009
