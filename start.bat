@echo off
cd /d "%~dp0"
title Canvas 10027

REM If LAN firewall rule is missing, try one UAC elevation (non-fatal if cancelled).
netsh advfirewall firewall show rule name="QingZi Canvas 10027" >nul 2>&1
if errorlevel 1 (
  echo [LAN] Firewall rule missing - requesting Administrator once...
  echo [LAN] If UAC appears, click Yes. Cancel is OK - server still starts for this PC.
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { Start-Process -FilePath '%~dp0ensure-lan-access.bat' -Verb RunAs -Wait -ErrorAction Stop } catch { exit 0 }"
) else (
  call "%~dp0ensure-lan-access.bat"
)

call "%~dp0free-port.bat" 8088 8089 10027

set "DOUBAO_PORT=10027"

call "%~dp0scripts\ensure-python-deps.bat"
if errorlevel 1 (
  echo.
  echo ERROR: Python dependencies failed. See messages above.
  echo.
  pause
  exit /b 1
)
if not defined PY (
  if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo ERROR: Python not found.
  echo Install Python 3.10+ or run: python -m venv .venv
  echo.
  pause
  exit /b 1
)

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=1" %%b in ("%%a") do set "LAN_IP=%%b"
)

echo.
echo Starting job worker on http://127.0.0.1:8089
start "Canvas Job Worker 8089" cmd /k "cd /d "%~dp0" & "%PY%" -u -m backend.run_worker"

echo.
echo Canvas %DOUBAO_PORT%
echo Job worker 8089 is in the other window.
echo Keep this window open while the server is running.
echo.
echo   This PC:   http://127.0.0.1:%DOUBAO_PORT%
if defined LAN_IP echo   Other PCs: http://%LAN_IP%:%DOUBAO_PORT%
echo.
echo If LAN still fails: set Wi-Fi to Private, or right-click ensure-lan-access.bat
echo   -^> Run as administrator.
echo.

"%PY%" -u run.py

echo.
echo Server stopped. Exit code: %ERRORLEVEL%
pause
