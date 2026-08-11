@echo off
if /I "%~1"=="__inner" goto inner
start "OLE-COM LoadSettings test - stay open" cmd /k ""%~f0" __inner"
exit /b

:inner
setlocal
cd /d "%~dp0"
set "CONDA_ROOT=C:\anaconda"
set "PY_EXE="

if exist "%CONDA_ROOT%\Scripts\activate.bat" (
    call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%"
    set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\DLLs;%PATH%"
    set "PY_EXE=python"
    goto :run
)
if exist "%CONDA_ROOT%\python.exe" (
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
set "MPS=%CD%\tools\olecom_smoke_peis.mps"
set "LOG=%CD%\results\olecom_admin_load_test_manual.log"
echo.
echo === OLE-COM LoadSettings smoke test ===
echo Open EC-Lab first, connect the instrument, then run this test.
echo This connects/selects channel and loads settings; it does not intentionally start a full measurement.
echo Python: %PY_EXE%
echo Log:    %LOG%
echo.
"%PY_EXE%" -c "from pathlib import Path; import sys; sys.path.insert(0, r'%CD%\tools'); from run_olecom_pre_scout_post_hybrid import _write_peis_mps; _write_peis_mps(Path(r'%MPS%'), bias_v=0.0, f_high_hz=100.0, f_low_hz=0.5, points_per_decade=6, amplitude_mv=30.0, bandwidth=8)"
if not exist "%MPS%" (
    echo Failed to generate smoke MPS: %MPS%
    pause
    exit /b 1
)
"%PY_EXE%" "%CD%\tools\olecom_admin_load_test.py" --ip 192.109.209.128 --mps "%MPS%" --log "%LOG%"
echo.
if exist "%LOG%" type "%LOG%"
echo.
echo This window is intentionally staying open.
pause
exit /b
