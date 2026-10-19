@echo off
cd /d "%~dp0.."
set "CADDY_DIR=%CD%\caddy"
set "CADDY_EXE=%CADDY_DIR%\caddy.exe"
set "CADDYFILE=%CADDY_DIR%\Caddyfile"

echo.
echo Caddy HTTPS proxy -^> 127.0.0.1:8088
echo Config: %CADDYFILE%
echo Close this window to stop Caddy.
echo.

if not exist "%CADDY_EXE%" (
    echo [error] caddy.exe not found.
    echo Run scripts\setup_caddy.bat or scripts\install_caddy_offline.bat
    pause
    exit /b 1
)

if not exist "%CADDYFILE%" (
    echo [error] Caddyfile not found.
    pause
    exit /b 1
)

cd /d "%CADDY_DIR%"
"%CADDY_EXE%" validate --config Caddyfile
if errorlevel 1 (
    echo.
    echo [error] invalid Caddyfile
    pause
    exit /b 1
)

"%CADDY_EXE%" run --config Caddyfile
pause
