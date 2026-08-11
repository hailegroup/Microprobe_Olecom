@echo off
setlocal
title MFC step response test - stay open

set "ROOT=%~dp0"
cd /d "%ROOT%"

if exist "C:\anaconda\Scripts\activate.bat" (
  call "C:\anaconda\Scripts\activate.bat" "C:\anaconda"
)

if exist "C:\anaconda\python.exe" (
  set "PY=C:\anaconda\python.exe"
) else (
  set "PY=python"
)

echo === MFC step response test ===
echo This sends one MFC setpoint and logs RFD/RFX vs time.
echo Use it to compare our command path with the vendor DMFC utility behavior.
echo.
"%PY%" "tools\mfc_step_response_test.py"
echo.
echo Exit code %ERRORLEVEL%
echo This window is intentionally staying open.
pause
