@echo off
cd /d "%~dp0"
title Canvas Job Worker 8089

call "%~dp0free-port.bat" 8089

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

echo.
echo Job worker on http://127.0.0.1:8089
echo Keep this window open while generating.
echo.

"%PY%" -u -m backend.run_worker

echo.
echo Worker stopped. Exit code: %ERRORLEVEL%
pause
