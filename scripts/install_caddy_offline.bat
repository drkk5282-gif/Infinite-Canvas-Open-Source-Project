@echo off
cd /d "%~dp0"
echo.
echo Offline install: put caddy.exe or caddy.zip here:
echo   %~dp0caddy\vendor\
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_caddy.ps1"
pause
