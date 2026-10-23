@echo off
REM Renew the Let's Encrypt certificate (certificates last 90 days - run this
REM every month or two, right-click and "Run as administrator").
REM Afterwards, restart the server so it loads the new certificate.
setlocal
set CERTBOT=E:\PythonServer\PythonServer2\1.0\.venv\Scripts\certbot.exe

%CERTBOT% renew --webroot -w E:\PythonServer\PythonServer2\1.0\certbot_webroot --non-interactive
if errorlevel 1 (
  echo.
  echo Renewal FAILED - see the certbot log for details.
  pause
  exit /b 1
)
echo.
echo Renewal complete (or no renewal was needed yet).
echo Restart the server to load the new certificate.
pause
