@echo off
REM Wrapper utk double-click / Windows Task Scheduler -- pastikan working
REM directory benar (folder file ini). TIDAK perlu redirect/tee manual --
REM exporter.py sendiri sudah log ke console DAN ke file logs\ sekaligus
REM (lihat pipeline\export.py, setup_logging).
REM Python dicari otomatis lewat PATH -- lihat scripts\find_python.bat.

cd /d "%~dp0"
call scripts\find_python.bat
if errorlevel 1 exit /b %errorlevel%

"%PYTHON_EXE%" exporter.py --only "bronze_*" --skip-local

set EXITCODE=%ERRORLEVEL%
echo.
echo ==== Selesai (exit code %EXITCODE%) ====

REM TIDAK ada 'pause' -- window otomatis tertutup setelah ini SELAMA file .bat
REM di-double-click langsung (bukan dijalankan dgn mengetik nama file di CMD
REM yang sudah terbuka -- itu window punya CMD-nya sendiri, tidak akan tertutup
REM otomatis walau .bat-nya sudah selesai, ini perilaku dasar Windows).
exit /b %EXITCODE%
