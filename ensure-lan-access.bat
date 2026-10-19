@echo off
REM Allow LAN clients to reach Canvas / Worker ports.
REM Also set current Wi-Fi / Ethernet to Private when possible.
REM Safe to run repeatedly. Needs Administrator.
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo [LAN] Not running as Administrator - skip firewall / network-profile update.
  echo       Right-click this file -^> Run as administrator
  echo       or: Settings -^> Network -^> WLAN -^> set to Private.
  echo.
  exit /b 0
)

echo [LAN] Setting connected Wi-Fi / Ethernet to Private ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match 'WLAN|Wi-?Fi|Ethernet|Ethernet|LAN' -and $_.NetworkCategory -ne 'DomainAuthenticated' } | ForEach-Object { try { Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private; Write-Host ('[LAN] ' + $_.InterfaceAlias + ' -> Private') } catch { Write-Host ('[LAN] could not set ' + $_.InterfaceAlias + ': ' + $_.Exception.Message) } }"

echo [LAN] Ensuring inbound firewall allow for 10027 / 8089 (all profiles) ...
netsh advfirewall firewall delete rule name="QingZi Canvas 10027" >nul 2>&1
netsh advfirewall firewall delete rule name="QingZi Admin 8090" >nul 2>&1
netsh advfirewall firewall delete rule name="QingZi Worker 8089" >nul 2>&1

netsh advfirewall firewall add rule name="QingZi Canvas 10027" dir=in action=allow protocol=TCP localport=10027 profile=any >nul
netsh advfirewall firewall add rule name="QingZi Worker 8089" dir=in action=allow protocol=TCP localport=8089 profile=any >nul

if errorlevel 1 (
  echo [LAN] ERROR: failed to add firewall rules.
  exit /b 1
)

echo [LAN] OK - Private profile + firewall rules for 10027 / 8089.
echo       Other PCs: http://YOUR_LAN_IP:10027
exit /b 0
