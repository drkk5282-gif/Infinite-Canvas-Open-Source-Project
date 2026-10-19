@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PORT=10027"
if exist port.txt (
  for /f "usebackq delims=" %%p in ("port.txt") do set "PORT=%%p"
)

echo.
echo  Starting QingZi canvas...
echo  Port: %PORT%  (edit port.txt to change)
echo.

if exist "start.bat" (
  call "%~dp0start.bat"
  exit /b %ERRORLEVEL%
)

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

set DOUBAO_PORT=%PORT%
"%PY%" -u run.py
pause
