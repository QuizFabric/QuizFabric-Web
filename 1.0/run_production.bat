@echo off
:start
REM Production server startup script
REM For Windows: Uses Flask-SocketIO production-ready server
REM Note: For Linux/Unix, use: gunicorn -w 1 --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -b 0.0.0.0:80 wsgi:app
REM (-w 1 on purpose: live games live in process memory, so more workers would split them)

setlocal enabledelayedexpansion

REM Set the path to the virtual environment
set VENV_PATH=E:\PythonServer\PythonServer2\1.0\.venv
set PYTHON=%VENV_PATH%\Scripts\python.exe
set FLASK_ENV=production

%PYTHON% -m pip install -r requirements.txt

echo Starting production server (HTTPS on 0.0.0.0:443, HTTP redirect on 0.0.0.0:80)...
set QUIZFABRIC_SERVER=https://localhost:5710

%PYTHON% wsgi.py
echo Press any key to restart the server...
pause >nul
goto start