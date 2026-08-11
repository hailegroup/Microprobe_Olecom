@echo off
title Register EC-Lab OLE-COM - run as Administrator
echo === EC-Lab OLE-COM registration ===
echo This must be run with Administrator privileges.
echo Close EC-Lab before running this file.
echo.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo ERROR: This window is not Administrator.
  echo Right-click this BAT file and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

set "ECLAB_EXE="

for %%P in (
  "%ProgramFiles(x86)%\EC-Lab\EClab.exe"
  "%ProgramFiles%\EC-Lab\EClab.exe"
  "%ProgramFiles(x86)%\BioLogic\EC-Lab\EClab.exe"
  "%ProgramFiles%\BioLogic\EC-Lab\EClab.exe"
  "%ProgramFiles(x86)%\Bio-Logic\EC-Lab\EClab.exe"
  "%ProgramFiles%\Bio-Logic\EC-Lab\EClab.exe"
  "%ProgramFiles(x86)%\BioLogic Science Instruments\EC-Lab\EClab.exe"
  "%ProgramFiles%\BioLogic Science Instruments\EC-Lab\EClab.exe"
  "C:\EC-Lab\EClab.exe"
  "C:\EC-Lab V11.61\EClab.exe"
) do (
  if exist %%~P (
    set "ECLAB_EXE=%%~P"
    goto found_eclab
  )
)

if not exist "%ECLAB_EXE%" (
  echo ERROR: Could not find EClab.exe in the usual install paths.
  echo Edit this BAT and set ECLAB_EXE to the actual EC-Lab executable path.
  echo.
  pause
  exit /b 1
)

:found_eclab
echo Registering:
echo   "%ECLAB_EXE%" /regserver
"%ECLAB_EXE%" /regserver
echo.
echo Waiting briefly, then checking common COM ProgIDs...
timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$names=@('EClabCOM.EClabExe','ECLabCOM.ECLabExe','EClab.Application'); foreach($n in $names){try{$o=New-Object -ComObject $n; Write-Host ($n+' OK'); [Runtime.InteropServices.Marshal]::ReleaseComObject($o) | Out-Null}catch{Write-Host ($n+' FAIL: '+$_.Exception.Message)}}"

if exist "%windir%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" (
  echo.
  echo Checking again with 32-bit PowerShell...
  "%windir%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$names=@('EClabCOM.EClabExe','ECLabCOM.ECLabExe','EClab.Application'); foreach($n in $names){try{$o=New-Object -ComObject $n; Write-Host ($n+' OK'); [Runtime.InteropServices.Marshal]::ReleaseComObject($o) | Out-Null}catch{Write-Host ($n+' FAIL: '+$_.Exception.Message)}}"
)

echo.
echo If one ProgID says OK, OLE-COM registration is available for Python.
pause
