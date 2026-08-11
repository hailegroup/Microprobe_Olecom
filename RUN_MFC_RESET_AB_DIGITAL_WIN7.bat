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

echo Python was not found by this reset launcher.
pause
exit /b 1

:run
echo.
echo === MFC RESET A/B TO ZERO: DIGITAL CONTROL MODE ===
echo This sends raw setting 0 to both MFC channels after you type WRITE.
echo.
echo Using Python: %PY_EXE%
echo.
"%PY_EXE%" tools\mfc_write_smoke_test.py --digital-control --a 0 --b 0
echo.
pause
