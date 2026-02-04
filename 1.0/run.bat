:start
@echo off

REM Set the path to the virtual environment
set VENV_PATH=E:\PythonServer\PythonServer2\.venv
set PYTHON=%VENV_PATH%\Scripts\python.exe

%PYTHON% -m pip install -r requirements.txt

start "GUI Monitor" cmd /c "%PYTHON% gui.py"
%PYTHON% app.py
echo There has been an error. Restarting now...
goto start
