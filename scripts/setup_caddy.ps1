# Caddy installer for Canvas Server (Windows)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$CaddyDir = Join-Path $Root "caddy"
$VendorDir = Join-Path $PSScriptRoot "caddy\vendor"
$Exe = Join-Path $CaddyDir "caddy.exe"
$Zip = Join-Path $CaddyDir "caddy.zip"
$Example = Join-Path $PSScriptRoot "caddy\Caddyfile.example"
$Config = Join-Path $CaddyDir "Caddyfile"
$SslDir = Join-Path $CaddyDir "ssl"

$Urls = @(
    "https://github.com/caddyserver/caddy/releases/latest/download/caddy_windows_amd64.zip",
    "https://ghfast.top/https://github.com/caddyserver/caddy/releases/latest/download/caddy_windows_amd64.zip"
)

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

Write-Host ""
Write-Host "========================================"
Write-Host " Caddy setup for Canvas HTTPS"
Write-Host "========================================"
Write-Host "Target: $CaddyDir"
Write-Host ""

Ensure-Dir $CaddyDir
Ensure-Dir $SslDir
Ensure-Dir $VendorDir

if (Test-Path $Exe) {
    Write-Host "[skip] caddy.exe already exists"
}
elseif (Test-Path (Join-Path $VendorDir "caddy.exe")) {
    Write-Host "[1/2] copy caddy.exe from vendor ..."
    Copy-Item (Join-Path $VendorDir "caddy.exe") $Exe -Force
    Write-Host "[done] caddy.exe installed (offline)"
}
elseif (Test-Path (Join-Path $VendorDir "caddy.zip")) {
    Write-Host "[1/2] extract vendor\caddy.zip ..."
    Expand-Archive -Path (Join-Path $VendorDir "caddy.zip") -DestinationPath $CaddyDir -Force
    Write-Host "[done] caddy.exe installed (offline zip)"
}
else {
    $ok = $false
    foreach ($url in $Urls) {
        Write-Host "[1/3] download: $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $Zip -UseBasicParsing -TimeoutSec 120
            $ok = $true
            break
        }
        catch {
            Write-Host "[warn] failed: $($_.Exception.Message)"
        }
    }
    if (-not $ok) {
        Write-Host ""
        Write-Host "[error] download failed on this server."
        Write-Host "Offline option:"
        Write-Host "  1) On your PC download caddy_windows_amd64.zip"
        Write-Host "     https://github.com/caddyserver/caddy/releases/latest"
        Write-Host "  2) Copy zip or caddy.exe to:"
        Write-Host "     $VendorDir"
        Write-Host "  3) Run scripts\install_caddy_offline.bat"
        exit 1
    }

    Write-Host "[2/3] extract ..."
    Expand-Archive -Path $Zip -DestinationPath $CaddyDir -Force
    Remove-Item $Zip -Force -ErrorAction SilentlyContinue
    Write-Host "[done] caddy.exe installed"
}

if (-not (Test-Path $Config)) {
    if (Test-Path $Example) {
        Copy-Item $Example $Config -Force
        Write-Host "[done] created Caddyfile"
    }
}
else {
    Write-Host "[skip] Caddyfile already exists"
}

Write-Host ""
Write-Host "Next:"
Write-Host "  1) Put certs in $SslDir"
Write-Host "     fullchain.pem + privkey.pem"
Write-Host "  2) Edit $Config (domain name)"
Write-Host "  3) Open TCP 443 in Tencent Cloud security group"
Write-Host "  4) Start canvas app, then run scripts\start_caddy.bat"
Write-Host ""
