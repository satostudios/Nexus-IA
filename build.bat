@echo off
rem Build dist\NEXUS.exe.     build.bat          single-file executable
rem                           build.bat onedir   folder build (starts faster)
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    exit /b 1
)
call ".venv\Scripts\activate.bat"

echo [1/4] Installing build tools
python -m pip install -r requirements-dev.txt --quiet
if errorlevel 1 exit /b 1

echo [2/4] Rendering icon
python -m scripts.make_icon
if errorlevel 1 exit /b 1

echo [3/4] Running PyInstaller
if /i "%~1"=="onedir" ( set NEXUS_ONEDIR=1 ) else ( set NEXUS_ONEDIR= )
python -m PyInstaller --noconfirm --clean nexus.spec
if errorlevel 1 ( echo [!] PyInstaller failed. & exit /b 1 )

if /i "%~1"=="onedir" ( set "EXE=dist\NEXUS\NEXUS.exe" ) else ( set "EXE=dist\NEXUS.exe" )
echo [4/4] Self-test of the packaged build: %EXE% --selftest
start "" /wait "%EXE%" --selftest
if errorlevel 1 (
    echo [!] Self-test FAILED. See logs\selftest.json next to the executable.
    exit /b 1
)
echo.
echo   Built %EXE%  ^(self-test passed^)
endlocal
