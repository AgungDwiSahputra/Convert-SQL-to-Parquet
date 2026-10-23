@echo off
REM Worker: mengeksekusi run yang diantrikan dari Web UI (berjalan terus, cek antrian tiap 3 detik).
REM Log tiap run: logs\runs\run_<id>.log. Jalankan SATU instance saja per server.
cd /d "%~dp0"
call scripts\find_python.bat
if errorlevel 1 exit /b %errorlevel%
"%PYTHON_EXE%" worker.py
exit /b %ERRORLEVEL%
