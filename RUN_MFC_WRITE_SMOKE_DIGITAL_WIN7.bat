@echo off
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

echo Python was not found by this digital write-test launcher.
pause
exit /b 1

:run
echo.
echo === MFC WRITE smoke test: DIGITAL CONTROL MODE ===
echo This sends the digital-control command first, then raw setting commands.
echo Use only after readback diagnostic passes and gases are in a safe state.
echo Suggested first raw targets: A=5 setting, B=5 setting.
echo.
echo Using Python: %PY_EXE%
echo.
"%PY_EXE%" tools\mfc_write_smoke_test.py --digital-control --a 5 --b 5
echo.
pause
