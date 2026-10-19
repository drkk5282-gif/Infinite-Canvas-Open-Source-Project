@echo off
REM Free listening ports before start. Usage: call free-port.bat 10027 8090 8089
:loop
if "%~1"=="" goto done
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%~1 .*LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
shift
goto loop
:done
timeout /t 1 /nobreak >nul
