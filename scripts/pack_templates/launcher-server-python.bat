@echo off
cd /d "%~dp0"
set DOUBAO_DATA_DIR=%~dp0data

set "PORT=10027"
if exist port.txt (
  for /f "usebackq delims=" %%p in ("port.txt") do set "PORT=%%p"
)

set "HOST=0.0.0.0"
if exist host.txt (
  for /f "usebackq delims=" %%h in ("host.txt") do set "HOST=%%h"
)

title Canvas %PORT%
set DOUBAO_PORT=%PORT%
set DOUBAO_HOST=%HOST%

call "%~dp0scripts\ensure-python-deps.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

"%PY%" -u run.py
if errorlevel 1 pause
