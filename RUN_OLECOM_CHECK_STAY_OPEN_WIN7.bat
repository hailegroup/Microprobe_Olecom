@echo off
if /I "%~1"=="__inner" goto inner
start "EC-Lab OLE-COM check - stay open" cmd /k ""%~f0" __inner"
exit /b

:inner
cd /d "%~dp0"
echo.
echo === EC-Lab OLE-COM check ===
echo This checks whether Windows can see the EC-Lab OLE-COM server.
echo It does not register anything and does not start a measurement.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$names=@('EClabCOM.EClabExe','ECLabCOM.ECLabExe','EClab.Application'); foreach($n in $names){try{$o=New-Object -ComObject $n; Write-Host ($n+' OK'); [Runtime.InteropServices.Marshal]::ReleaseComObject($o) | Out-Null}catch{Write-Host ($n+' FAIL: '+$_.Exception.Message)}}"

echo.
echo If all entries fail with class-not-registered, run RUN_OLECOM_REGISTER_ADMIN_WIN7.bat as Administrator.
echo This window is intentionally staying open.
pause
exit /b
