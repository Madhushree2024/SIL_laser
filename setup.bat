@echo off
setlocal
cd /d "%~dp0"
if exist ".venv-windows\Scripts\python.exe" goto :install
where py >nul 2>nul
if errorlevel 1 (
    set "BOOTSTRAP=python"
) else (
    set "BOOTSTRAP=py -3"
)
%BOOTSTRAP% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo Install Python 3.11 or newer with the Python launcher or add Python to PATH.
    if /i not "%~1"=="/quiet" pause
    exit /b 1
)
if not exist ".venv-windows\Scripts\python.exe" (
    %BOOTSTRAP% -m venv .venv-windows
    if errorlevel 1 goto :failed
)
:install
".venv-windows\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-desktop.txt
if errorlevel 1 goto :failed
echo Setup complete. Run "Start Automation.cmd" in this folder.
if /i not "%~1"=="/quiet" pause
exit /b 0
:failed
echo Setup failed. Review the error above and run setup.bat again.
if /i not "%~1"=="/quiet" pause
exit /b 1
