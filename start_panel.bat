@echo off
REM Starts the panel with the project's virtual environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Cannot find the .venv environment. Follow the "Installation" section of the README.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
