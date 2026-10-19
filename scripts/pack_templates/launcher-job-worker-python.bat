@echo off
cd /d "%~dp0"
set DOUBAO_DATA_DIR=%~dp0data

title Job Worker 8089
set CANVAS_ROLE=worker

call "%~dp0scripts\ensure-python-deps.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

"%PY%" -u run.py
if errorlevel 1 pause
