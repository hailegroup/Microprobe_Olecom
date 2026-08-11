@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT_DIR=%~dp0"
set "PYFILE=%SCRIPT_DIR%build_opju_from_folder.py"

rem Folder can be dragged onto this .bat or passed as an argument.
rem If left empty, a folder picker opens so you can select the condition folder.
set "FOLDER=%~1"
if defined FOLDER set "FOLDER=%FOLDER:"=%"

rem Locate a Python interpreter (same preference order as the GUI launcher).
set "SHARED_VENV_PY=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
set "CONDA_ROOT=C:\anaconda"
set "PYEXE="
if exist "%SHARED_VENV_PY%" set "PYEXE=%SHARED_VENV_PY%"
if not defined PYEXE if exist "%CONDA_ROOT%\python.exe" set "PYEXE=%CONDA_ROOT%\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYEXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYEXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
    echo Python was not found. Edit this file to point at your python.exe.
    pause
    exit /b 1
)

echo Using Python: %PYEXE%
echo.
echo Layout:
echo   [1] One .opju per ELECTRODE (default) - a folder per gas condition, voltage-named workbooks
echo   [2] One MASTER .opju for the whole dataset - workbook per row + 7 graphs per folder
echo   [3] One .opju per row, mirrored into a folder tree
set "LAYOUT=--layout electrode"
set /p "CHOICE=Choose 1, 2 or 3 (Enter = 1): "
if "%CHOICE%"=="2" set "LAYOUT=--layout master"
if "%CHOICE%"=="3" set "LAYOUT=--layout per-row"

echo.
if defined FOLDER (
    echo Building Origin .opju from: %FOLDER%  %LAYOUT%
    echo.
    "%PYEXE%" "%PYFILE%" "%FOLDER%" %LAYOUT%
) else (
    echo No folder given - opening a folder picker...  %LAYOUT%
    echo.
    "%PYEXE%" "%PYFILE%" %LAYOUT%
)
echo.
pause
