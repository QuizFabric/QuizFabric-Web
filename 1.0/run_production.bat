@echo off
:start
REM Production server startup script
REM For Windows: Uses Flask-SocketIO production-ready server
REM Note: For Linux/Unix, use: gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -b 0.0.0.0:5710 -w 4 wsgi:app

setlocal enabledelayedexpansion

REM Set the path to the virtual environment
set VENV_PATH=E:\PythonServer\PythonServer2\1.0\.venv
set PYTHON=%VENV_PATH%\Scripts\python.exe
set FLASK_ENV=production

%PYTHON% -m pip install -r requirements.txt

echo Starting production server on 0.0.0.0:5710...
REM cd /d "e:\PythonServer\PythonServer2\1.0"
start "GUI Monitor" cmd /c "%PYTHON% gui.py"
%PYTHON% wsgi.py
echo Press any key to restart the server...
pause >nul
goto start