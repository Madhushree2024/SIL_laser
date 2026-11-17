@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv-windows\Scripts\python.exe" (
    echo Run setup.bat first, or use the installer ZIP from GitHub Releases.
    pause
    exit /b 1
)
"%~dp0.venv-windows\Scripts\python.exe" "%~dp0tools\run_source.py"
if errorlevel 1 pause
