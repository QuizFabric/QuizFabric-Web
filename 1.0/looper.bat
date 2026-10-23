@echo off
set SOURCE=D:\iCloudDrive\QuizAvatars
set DEST=E:\PythonServer\PythonServer2\1.0\data\avatars

:loop
powershell -Command Copy-Item -Path "%SOURCE%\*" -Destination "%DEST%" -Force -Recurse
timeout /t 2 >nul
goto loop
