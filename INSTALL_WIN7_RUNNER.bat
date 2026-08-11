@echo off
setlocal
cd /d "%~dp0"

echo.
echo === Microprobe Windows 7 runner install ===
echo This creates/updates .venv_win7 and installs the minimal hardware runner packages.
echo Recommended Python: 3.8.10.
echo.

set "PY_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.8 -V >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=py -3.8"
)

if "%PY_CMD%"=="" (
    where python >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=python"
)

if "%PY_CMD%"=="" (
    if exist "C:\Python38\python.exe" set "PY_CMD=C:\Python38\python.exe"
)

if "%PY_CMD%"=="" (
    echo Python 3.8 was not found.
    echo Install Python 3.8.10 on the Windows 7 computer, then rerun this file.
    pause
    exit /b 1
)

echo Using Python command: %PY_CMD%
%PY_CMD% -m venv .venv_win7
if errorlevel 1 (
    echo.
    echo Failed to create .venv_win7.
    pause
    exit /b 1
)

call ".venv_win7\Scripts\activate.bat"
python -m pip install --upgrade "pip<25" "setuptools<81" wheel
if errorlevel 1 goto :install_failed

python -m pip install -r requirements-win7-runner.txt
if errorlevel 1 goto :install_failed

echo.
echo Install finished.
echo Next: edit config.py COM ports/IP if needed, then run START_HERE_WIN7_RUNNER.bat.
pause
exit /b 0

:install_failed
echo.
echo Install failed. Keep this window open and check the package error above.
pause
exit /b 1

