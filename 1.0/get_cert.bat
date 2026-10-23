@echo off
REM Get the Let's Encrypt certificate for play.lucasgeitgey.com
REM 1. Make sure the server is running (run.bat) so the ACME challenge is served.
REM 2. Right-click this file and choose "Run as administrator" (certbot requires it).
setlocal
set CERTBOT=E:\PythonServer\PythonServer2\1.0\.venv\Scripts\certbot.exe

%CERTBOT% certonly --webroot -w E:\PythonServer\PythonServer2\1.0\certbot_webroot -d play.lucasgeitgey.com --non-interactive --agree-tos --register-unsafely-without-email
if errorlevel 1 (
  echo.
  echo Certificate request FAILED.
  echo Check that the server is running and that play.lucasgeitgey.com reaches this network.
  pause
  exit /b 1
)
echo.
echo Done! Certificates are in C:\Certbot\live\play.lucasgeitgey.com\
echo Restart the server to start serving HTTPS on port 443.
pause
