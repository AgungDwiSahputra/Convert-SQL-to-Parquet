@echo off
REM Wrapper utk double-click / Windows Task Scheduler -- pastikan working
REM directory benar (supaya .env & folder output/ terbaca relatif ke lokasi
REM file ini). TIDAK perlu redirect/tee manual -- exporter.py sendiri sudah
REM log ke console DAN ke file logs\ sekaligus (lihat exporter.py, _LOG_FILE).

cd /d "%~dp0"

REM --- Cari python.exe otomatis lewat PATH (PORTABLE -- tidak hardcode path
REM user/komputer tertentu, supaya .bat ini tetap jalan kalau dipindah ke
REM server lain / akun Windows lain tanpa perlu diedit manual tiap kali). ---
REM 1) coba "python" biasa dulu.
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_EXE=python"
    goto :run
)

REM 2) fallback ke Python Launcher ("py") -- terdaftar system-wide di PATH oleh
REM    installer resmi python.org terlepas dari akun mana yang menginstallnya,
REM    jadi lebih tahan banting lintas server/akun dibanding path AppData user.
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_EXE=py"
    goto :run
)

echo ERROR: python.exe / py.exe tidak ditemukan di PATH komputer ini.
echo   - Pastikan Python terinstall dan "Add python.exe to PATH" tercentang
echo     saat instalasi, ATAU
echo   - Isi manual path lengkap python.exe di baris PYTHON_EXE di bawah ini.
REM set "PYTHON_EXE=C:\path\lengkap\ke\python.exe"
exit /b 9009

:run
"%PYTHON_EXE%" exporter.py --only "bronze_*" --skip-local

set EXITCODE=%ERRORLEVEL%
echo.
echo ==== Selesai (exit code %EXITCODE%) ====

REM TIDAK ada 'pause' -- window otomatis tertutup setelah ini SELAMA file .bat
REM di-double-click langsung (bukan dijalankan dgn mengetik nama file di CMD
REM yang sudah terbuka -- itu window punya CMD-nya sendiri, tidak akan tertutup
REM otomatis walau .bat-nya sudah selesai, ini perilaku dasar Windows).
exit /b %EXITCODE%
