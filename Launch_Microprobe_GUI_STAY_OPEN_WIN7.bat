@echo off
if /I "%~1"=="__inner" goto inner
start "Microprobe GUI - stay open" cmd /k ""%~f0" __inner"
exit /b

:inner
setlocal
cd /d "%~dp0"

set "GUI_FILE=%~dp0gui.py"
set "CONDA_ROOT=C:\anaconda"
set "PY_EXE="

echo.
echo === MICROPROBE GUI - STAY OPEN ===
echo Working dir: %CD%
echo GUI file: %GUI_FILE%
echo.

if exist "%CONDA_ROOT%\Scripts\activate.bat" (
    echo Activating Anaconda: %CONDA_ROOT%
    call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%"
    set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\DLLs;%PATH%"
    set "PY_EXE=python"
    goto :run
)

if exist "%CONDA_ROOT%\python.exe" (
    echo Anaconda activate.bat was not found; using python.exe with DLL PATH patched.
    set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\DLLs;%PATH%"
    set "PY_EXE=%CONDA_ROOT%\python.exe"
    goto :run
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_EXE=python"
    goto :run
)

echo Python was not found.
pause
exit /b 1

:run
echo Using Python: %PY_EXE%
echo PATH head:
echo %PATH%
echo.
"%PY_EXE%" "%GUI_FILE%"
echo.
echo GUI exited with code: %errorlevel%
echo This window is intentionally staying open.
pause
exit /b
