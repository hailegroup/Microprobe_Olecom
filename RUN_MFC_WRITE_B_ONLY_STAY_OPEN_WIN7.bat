@echo off
if /I "%~1"=="__inner" goto inner
start "MFC B-only write - stay open" cmd /k ""%~f0" __inner"
exit /b

:inner
setlocal
cd /d "%~dp0"

set "PY_EXE="

if exist "%~dp0.venv_win7\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv_win7\Scripts\python.exe"
    goto :run
)

if exist "%~dp0..\.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0..\.venv\Scripts\python.exe"
    goto :run
)

for %%P in (
    "C:\anaconda\python.exe"
    "C:\Anaconda\python.exe"
    "C:\Anaconda3\python.exe"
    "C:\Python38\python.exe"
    "C:\Python37\python.exe"
    "%USERPROFILE%\anaconda3\python.exe"
    "%USERPROFILE%\miniconda3\python.exe"
    "C:\ProgramData\Anaconda3\python.exe"
    "C:\ProgramData\Miniconda3\python.exe"
) do (
    if exist %%~P (
        set "PY_EXE=%%~P"
        goto :run
    )
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_EXE=py"
    goto :run
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_EXE=python"
    goto :run
)

echo Python was not found by this B-only write launcher.
echo.
echo This window will stay open. Press any key when done.
pause
exit /b 1

:run
echo.
echo === MFC WRITE B ONLY - STAY OPEN ===
echo This sends A=0, B=5 with digital-control mode after you type WRITE.
echo Working dir: %CD%
echo Using Python: %PY_EXE%
echo.
"%PY_EXE%" tools\mfc_write_smoke_test.py --digital-control --a 0 --b 5
echo.
echo Exit code: %errorlevel%
echo This window is intentionally staying open.
pause
exit /b
