Offline Caddy install (when server cannot download from GitHub)

1) On your local PC, download:
   https://github.com/caddyserver/caddy/releases/latest/download/caddy_windows_amd64.zip

2) Copy ONE of these into this folder (vendor):
   - caddy.exe
   - caddy.zip

3) Run on server:
   scripts\install_caddy_offline.bat

4) Put SSL certs in:
   caddy\ssl\fullchain.pem
   caddy\ssl\privkey.pem

5) Edit:
   caddy\Caddyfile

6) Start:
   scripts\start_caddy.bat
