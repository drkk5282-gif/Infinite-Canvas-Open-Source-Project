@echo off
cd /d "%~dp0"
set "INTERNAL=10027"
set "EXTERNAL=10028"
if exist "%~dp0..\port.txt" for /f "usebackq delims=" %%p in ("%~dp0..\port.txt") do set "INTERNAL=%%p"
if exist "%~dp0..\external_port.txt" for /f "usebackq delims=" %%p in ("%~dp0..\external_port.txt") do set "EXTERNAL=%%p"

echo.
echo ===== Caddy / network diagnose =====
echo canvas internal : %INTERNAL%
echo caddy public    : %EXTERNAL%
echo.

echo [1] canvas http://127.0.0.1:%INTERNAL%
curl -s -o nul -w "HTTP %%{http_code}\n" http://127.0.0.1:%INTERNAL% 2>nul
if errorlevel 1 echo FAIL - start canvas first

echo.
echo [2] caddy https://127.0.0.1:%EXTERNAL%
curl -k -s -o nul -w "HTTP %%{http_code}\n" https://127.0.0.1:%EXTERNAL% 2>nul
if errorlevel 1 echo FAIL - caddy not listening on %EXTERNAL%

echo.
echo [3] listening ports
netstat -ano | findstr ":%INTERNAL%"
netstat -ano | findstr ":%EXTERNAL%"

echo.
echo [4] caddy process
tasklist | findstr /I caddy.exe
if errorlevel 1 echo caddy.exe NOT running

echo.
echo [5] firewall rule for %EXTERNAL%
netsh advfirewall firewall show rule name="Canvas HTTPS %EXTERNAL%" >nul 2>&1
if errorlevel 1 (
    echo missing - run as admin:
    echo netsh advfirewall firewall add rule name="Canvas HTTPS %EXTERNAL%" dir=in action=allow protocol=TCP localport=%EXTERNAL%
) else (
    echo OK
)

echo.
pause
