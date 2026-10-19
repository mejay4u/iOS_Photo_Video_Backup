@echo off
rem Photo Backup launcher: creates a private Python environment on first run, then starts the app.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up, this takes a minute...
    %PY% -m venv .venv || (echo Python 3.9+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Installing packages failed. & pause & exit /b 1)
)
".venv\Scripts\python.exe" run.py %*
pause
