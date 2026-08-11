@echo off
if /I "%~1"=="__inner" goto inner
start "Install OLE-COM deps - stay open" cmd /k ""%~f0" __inner"
exit /b

:inner
setlocal
cd /d "%~dp0"

echo.
echo === Install/check OLE-COM Python dependencies ===
echo This fixes: No module named 'galvani'
echo It installs only the small packages needed by the OLE-COM BioLogic backend.
echo.

set "PY_EXE="
set "CONDA_ROOT=C:\anaconda"

if exist "%CONDA_ROOT%\Scripts\activate.bat" (
    call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%"
    set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\DLLs;%PATH%"
    set "PY_EXE=python"
    goto found_python
)

if exist "%CONDA_ROOT%\python.exe" (
    set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\DLLs;%PATH%"
    set "PY_EXE=%CONDA_ROOT%\python.exe"
    goto found_python
)

if exist "%~dp0.venv_win7\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv_win7\Scripts\python.exe"
    goto found_python
)

for %%P in (
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
        goto found_python
    )
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_EXE=python"
    goto found_python
)

echo Python was not found.
echo If the old GUI runs with a specific python.exe, edit this BAT and set PY_EXE to that path.
pause
exit /b 1

:found_python
echo Using Python: %PY_EXE%
"%PY_EXE%" -V
echo.

echo Checking current imports...
"%PY_EXE%" -c "import comtypes.client; from galvani import BioLogic; print('OLE-COM deps already OK')" 2>nul
if %errorlevel%==0 goto success

echo.
echo Installing/updating comtypes and galvani...
"%PY_EXE%" -m pip install comtypes galvani
if errorlevel 1 goto install_failed

echo.
echo Re-checking imports...
"%PY_EXE%" -c "import comtypes.client; from galvani import BioLogic; print('OLE-COM deps OK')"
if errorlevel 1 goto install_failed

:success
echo.
echo Done. Close and reopen the Microprobe GUI, then press Connect for BioLogic SP-200 (OLE-COM).
pause
exit /b 0

:install_failed
echo.
echo Install/check failed.
echo If this computer has no internet or pip has SSL trouble, copy offline wheels for comtypes and galvani and install with:
echo   %PY_EXE% -m pip install --no-index --find-links C:\path\to\wheels comtypes galvani
echo.
pause
exit /b 1
