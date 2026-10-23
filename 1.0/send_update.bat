@echo off
REM Build the update payload for the Raspberry Pi and start the update host.
REM The Pi checks for updates at every boot and pulls this payload.
setlocal
set PYTHON=E:\PythonServer\PythonServer2\1.0\.venv\Scripts\python.exe

%PYTHON% build_update.py
if errorlevel 1 (
  echo Update build FAILED.
  pause
  exit /b 1
)

start "QuizFabric Update Host" cmd /c "%PYTHON% update_host.py"
echo.
echo Update host is running on port 8000.
echo The Raspberry Pi will download the update at its next boot.
echo Keep this machine on (or re-run when the Pi boots).
pause
