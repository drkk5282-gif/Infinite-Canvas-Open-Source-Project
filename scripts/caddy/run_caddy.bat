@echo off
cd /d "%~dp0"
set "CADDY_EXE=%~dp0caddy.exe"
set "CADDYFILE=%~dp0Caddyfile"
set "INTERNAL=8088"
set "EXTERNAL=8088"

if exist "%~dp0..\port.txt" (
    for /f "usebackq delims=" %%p in ("%~dp0..\port.txt") do set "INTERNAL=%%p"
)
set "EXTERNAL=%INTERNAL%"
if exist "%~dp0..\external_port.txt" (
    for /f "usebackq delims=" %%p in ("%~dp0..\external_port.txt") do set "EXTERNAL=%%p"
)

echo.
echo ========================================
echo  Caddy public :%EXTERNAL%  -^>  canvas 127.0.0.1:%INTERNAL%
echo ========================================
echo  Folder : %~dp0
echo  port.txt          = canvas (%INTERNAL%)
echo  external_port.txt = public HTTPS (%EXTERNAL%)
echo  Domain : edit domain.txt
echo.
echo  Do NOT double-click caddy.exe
echo.

if not exist "%CADDY_EXE%" (
    echo [error] caddy.exe not found in this folder.
    pause
    exit /b 1
)

if not exist "%~dp0domain.txt" (
    if exist "%~dp0domain.txt.example" (
        copy /y "%~dp0domain.txt.example" "%~dp0domain.txt" >nul
        echo [info] created domain.txt - edit your domain first.
    ) else (
        echo [error] domain.txt not found.
        pause
        exit /b 1
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate_caddyfile.ps1"
if errorlevel 1 (
    echo [error] failed to generate Caddyfile
    pause
    exit /b 1
)

if not exist "%~dp0ssl\fullchain.pem" (
    echo [warn] ssl\fullchain.pem missing - put certs in caddy\ssl\
)

tasklist /FI "IMAGENAME eq caddy.exe" 2>nul | find /I "caddy.exe" >nul
if not errorlevel 1 (
    echo [warn] caddy.exe already running - stop old window first, or run:
    echo        taskkill /F /IM caddy.exe
    pause
    exit /b 1
)

"%CADDY_EXE%" validate --config "%CADDYFILE%"
if errorlevel 1 (
    echo [error] Caddyfile invalid
    pause
    exit /b 1
)

echo [ok] starting Caddy... close this window to stop.
echo.
"%CADDY_EXE%" run --config "%CADDYFILE%"
echo.
echo Caddy stopped.
pause
