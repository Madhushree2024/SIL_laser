@echo off
setlocal
cd /d "%~dp0"
title Separate Sheath Detection Live Test
echo Close the normal Sheath Template Cutting app first so the USB camera is free.
echo Q or Esc closes the preview. S saves results. M shows the mask.
"%~dp0.venv-windows\Scripts\python.exe" -B "%~dp0sheath_detection_experimental.py" --live --compare-current %*
if errorlevel 1 pause
