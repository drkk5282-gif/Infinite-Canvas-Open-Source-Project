@echo off
cd /d "%~dp0"
set DOUBAO_DATA_DIR=%~dp0data

title Job Worker 8089
set CANVAS_ROLE=worker
CanvasServer.exe
if errorlevel 1 pause
