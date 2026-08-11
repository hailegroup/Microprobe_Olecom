@echo off
setlocal
cd /d "%~dp0"

echo.
echo === Microprobe Windows 7 runner ===
echo Runs the GUI from this folder without Codex.
echo.

set "PY_EXE=%~dp0.venv_win7\Scripts\python.exe"
set "FOUND_PY="

if exist "%PY_EXE%" (
    "%PY_EXE%" gui.py
    goto :done
)

if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" gui.py
    goto :done
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
        set "FOUND_PY=%%~P"
        goto :run_found
    )
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.8 gui.py
    if not errorlevel 1 goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python gui.py
    goto :done
)

echo Python was not found.
echo Run INSTALL_WIN7_RUNNER.bat first, or install Python 3.8.10.
goto :done

:run_found
"%FOUND_PY%" gui.py

:done
if errorlevel 1 (
    echo.
    echo GUI exited with an error. Keep this window open and read the message above.
    pause
)
