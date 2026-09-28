@echo off
REM Lanza el panel con el entorno virtual del proyecto.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo No encuentro el entorno .venv. Sigue la seccion "Instalacion" del README.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
