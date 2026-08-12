@echo off
REM Double-click this to (re)build backend\data\historical.db.
REM Same thing as running:  python backend\data\seed_historical.py
REM Just makes sure it uses this repo's venv and keeps the window open
REM so you can actually read the result instead of it flashing shut.

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Can't find venv\Scripts\python.exe next to this file.
    echo Set up the venv first ^(see backend\README.md^), then try again.
    pause
    exit /b 1
)

venv\Scripts\python.exe backend\data\seed_historical.py %*

echo.
pause
