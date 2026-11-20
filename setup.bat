@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title NEXUS setup
echo.
echo   NEXUS setup
echo   ===========
echo.

rem --- 1. Python --------------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python was not found.
    echo     Install Python 3.10 - 3.13 from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH" during installation.
    exit /b 1
)
python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)"
if errorlevel 1 (
    echo [!] NEXUS needs Python 3.10, 3.11, 3.12 or 3.13. Found:
    python --version
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo [1/6] %%v found

rem --- 2. Virtual environment -------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/6] Creating virtual environment .venv
    python -m venv .venv
    if errorlevel 1 ( echo [!] Could not create the virtual environment. & exit /b 1 )
) else (
    echo [2/6] Using existing virtual environment .venv
)
call ".venv\Scripts\activate.bat"

rem --- 3. Dependencies --------------------------------------------------------
echo [3/6] Installing dependencies
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [!] Dependency installation failed. & exit /b 1 )

rem --- 4. cactus-needle -------------------------------------------------------
echo [4/6] Installing cactus-needle (Needle 3)
python -m pip install "cactus-needle==3.0.5" --quiet
if errorlevel 1 ( echo [!] cactus-needle installation failed. & exit /b 1 )

rem --- 5. Local models --------------------------------------------------------
echo [5/6] Preparing local models (Needle 3 engine + weights, speech recognition, voice)
python -m scripts.prepare_assets
if errorlevel 1 ( echo [!] Model preparation failed. Check your internet connection and run setup.bat again. & exit /b 1 )

rem --- 6. Verify audio, voice and Needle --------------------------------------
echo [6/6] Verifying microphone, voice and Needle
python -m scripts.prepare_assets --verify
if errorlevel 1 (
    echo [!] Verification reported a problem ^(see above^). NEXUS may still run; check Settings ^> Audio.
    exit /b 1
)

echo.
echo   Setup complete. Start NEXUS with run.bat
echo.
endlocal
