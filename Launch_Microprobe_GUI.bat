@echo off
cd /d "%~dp0"

set "SCRIPT_DIR=%~dp0"
set "GUI_FILE=%SCRIPT_DIR%gui.py"
set "SHARED_VENV_PY=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
set "FOUND_PY="
set "PY_CMD="
set "CONDA_ROOT=C:\anaconda"

if exist "%SHARED_VENV_PY%" (
    "%SHARED_VENV_PY%" "%GUI_FILE%"
    if errorlevel 1 (
        echo.
        echo Launch failed with the shared Microprobe virtual environment. Press any key to close.
        pause >nul
    )
    goto :eof
)

if exist "%CONDA_ROOT%\Scripts\activate.bat" (
    call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%"
    set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\DLLs;%PATH%"
    python "%GUI_FILE%"
    if errorlevel 1 (
        echo.
        echo Launch failed after activating Anaconda. Press any key to close.
        pause >nul
    )
    goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py"
    goto :run
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=python"
    goto :run
)

for %%P in (
    "C:\anaconda\python.exe"
    "%USERPROFILE%\anaconda3\python.exe"
    "%USERPROFILE%\miniconda3\python.exe"
    "C:\Users\%USERNAME%\anaconda3\python.exe"
    "C:\Users\%USERNAME%\miniconda3\python.exe"
    "C:\ProgramData\Anaconda3\python.exe"
    "C:\ProgramData\Miniconda3\python.exe"
) do (
    if exist %%~P (
        set "FOUND_PY=%%~P"
        goto :run_found
    )
)

for /f "delims=" %%P in ('where /r "%USERPROFILE%" python.exe 2^>nul') do (
    set "FOUND_PY=%%P"
    goto :run_found
)

for /f "delims=" %%P in ('where /r "C:\ProgramData" python.exe 2^>nul') do (
    set "FOUND_PY=%%P"
    goto :run_found
)

echo Python launcher was not found.
echo Anaconda may be installed, but its python.exe was not found in PATH or common install locations.
echo If needed, edit this file and replace the path check with the actual python.exe location.
pause
goto :eof

:run
%PY_CMD% "%GUI_FILE%"
if errorlevel 1 (
    echo.
    echo Launch failed. Press any key to close.
    pause >nul
)
goto :eof

:run_found
set "FOUND_DIR=%~dp0"
"%FOUND_PY%" "%GUI_FILE%"
if errorlevel 1 (
    echo.
    echo Launch failed. Press any key to close.
    pause >nul
)
