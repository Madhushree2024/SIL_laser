@echo off
setlocal
cd /d "%~dp0"
title Areca Cutter
set "ARECA_PYTHON=%~dp0.venv-windows\Scripts\python.exe"
if not exist "%ARECA_PYTHON%" (
    call "%~dp0setup.bat" /quiet
    if errorlevel 1 goto :failed
)
"%ARECA_PYTHON%" -c "import fastapi, uvicorn, cv2, numpy, shapely, yaml, ezdxf, multipart" >nul 2>nul
if errorlevel 1 (
    call "%~dp0setup.bat" /quiet
    if errorlevel 1 goto :failed
)
"%ARECA_PYTHON%" "%~dp0launch.py" %*
if errorlevel 1 goto :failed
exit /b 0
:failed
echo Areca Cutter could not start. Review the error above.
pause
exit /b 1
