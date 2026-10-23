@echo off
REM Helper dipanggil run_exporter.bat / run_web.bat / run_worker.bat: isi PYTHON_EXE dengan
REM python yang ada di PATH. PORTABLE -- tidak hardcode path user/komputer tertentu, supaya .bat
REM tetap jalan kalau dipindah ke server / akun Windows lain tanpa perlu diedit manual:
REM   1) "python" biasa;
REM   2) Python Launcher "py" -- terdaftar system-wide di PATH oleh installer resmi python.org
REM      terlepas dari akun mana yang menginstallnya.
REM PYTHON_EXE yang sudah di-set sebelumnya (mis. path lengkap python.exe di setelan Task
REM Scheduler / service) dipakai apa adanya.
if defined PYTHON_EXE exit /b 0

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
echo   - Pastikan Python terinstall dan "Add python.exe to PATH" tercentang saat instalasi, ATAU
echo   - set PYTHON_EXE=C:\path\lengkap\ke\python.exe sebelum menjalankan .bat ini.
exit /b 9009
