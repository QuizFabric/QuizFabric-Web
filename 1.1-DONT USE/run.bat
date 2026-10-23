:start
@echo off
pip install -r requirements.txt

python app.py
echo There has been an error. Restarting now...
goto start
