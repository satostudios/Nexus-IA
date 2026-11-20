@echo off
rem Development launcher.   run.bat            start NEXUS (no console window)
rem                         run.bat --console  start with a console for debugging
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
if /i "%~1"=="--console" (
    ".venv\Scripts\python.exe" -m app.main
    exit /b %errorlevel%
)
start "" ".venv\Scripts\pythonw.exe" -m app.main %*
