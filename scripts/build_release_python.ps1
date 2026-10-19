# Build lightweight Python source pack: dist/CanvasServer-py/
# No PyInstaller / no Chromium / MyApi only — lower RAM on servers with Python.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Root: $Root"
Write-Host "==> Profile: Python source pack (MyApi only, no exe)"

$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) { $pyCmd = Get-Command py -ErrorAction SilentlyContinue }
if (-not $pyCmd) { throw "Python not found. Install Python 3.10+ first." }
$py = $pyCmd.Source
Write-Host "==> Python: $py"

$Out = Join-Path $Root "dist\CanvasServer-py"
Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Write-Host "==> Bump version..."
$versionJson = & $py (Join-Path $Root "scripts\bump_version.py") "CanvasServer"
$version = $versionJson | ConvertFrom-Json
Write-Host "==> Version: v$($version.version) build $($version.build)"

Write-Host "==> Bump static cache (?v=b$($version.build))..."
& $py (Join-Path $Root "scripts\bump_static_cache.py")

Write-Host "==> Copying source tree..."
Copy-Item -Recurse (Join-Path $Root "frontend") (Join-Path $Out "frontend")
Copy-Item -Recurse (Join-Path $Root "backend") (Join-Path $Out "backend")
Copy-Item -Force (Join-Path $Root "run.py") (Join-Path $Out "run.py")
Copy-Item -Force (Join-Path $Root "requirements-server.txt") (Join-Path $Out "requirements.txt")
Copy-Item -Force (Join-Path $Root "version.json") (Join-Path $Out "version.json")

# Remove watermark client from pack
$zacaoPy = Join-Path $Out "backend\zacao_parser.py"
if (Test-Path $zacaoPy) {
  Remove-Item -Force $zacaoPy
  Write-Host "==> Removed backend/zacao_parser.py"
}

# Drop __pycache__ / tests noise
Get-ChildItem -Path $Out -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$enginesSrc = Join-Path $Root "tools\engines"
$enginesDst = Join-Path $Out "tools\engines"
if (Test-Path $enginesSrc) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Out "tools") | Out-Null
  Copy-Item -Recurse $enginesSrc $enginesDst
  Write-Host "==> Copied tools/engines"
}

$profile = @{
  disable_browser_channels = $true
  disable_doubao           = $true
  disable_watermark        = $true
  product_label            = "Canvas Server"
  skip_playwright_pack     = $true
  pack_kind                = "python"
  version                  = $version.version
  build                    = [int]$version.build
  built_at                 = $version.built_at
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText(
  (Join-Path $Out "build_profile.json"),
  $profile,
  [System.Text.UTF8Encoding]::new($false)
)
Write-Host "==> Wrote build_profile.json"

& $py (Join-Path $Root "scripts\init_pack_data.py") (Join-Path $Out "data") server
& $py (Join-Path $Root "scripts\stage_pack_extras.py") $Out server-python
# requirements must survive stage_pack_extras cleanup
Copy-Item -Force (Join-Path $Root "requirements-server.txt") (Join-Path $Out "requirements.txt")
& $py (Join-Path $Root "scripts\sanitize_api_only_ui.py") $Out

$zip = Join-Path $Root "dist\CanvasServer-py-portable.zip"
Write-Host "==> Zipping to $zip (data/ excluded)..."
& $py (Join-Path $Root "scripts\zip_release.py") $Out $zip --exclude-data

Write-Host "==> Building update pack..."
& $py (Join-Path $Root "scripts\release_pack.py") finalize $Out --product CanvasServer-py --full-zip $zip

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
$updateZip = Get-ChildItem -Path (Join-Path $Root "dist") -Filter "CanvasServer-py-update-*.zip" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Write-Host ""
Write-Host "DONE (Python source pack — MyApi only)."
Write-Host "  Folder : $Out"
Write-Host "  Zip    : $zip  ($size MB)"
if ($updateZip) {
  $uSize = [math]::Round($updateZip.Length / 1MB, 2)
  Write-Host "  Update : $($updateZip.FullName)  ($uSize MB)"
}
Write-Host "  Source pack: install requirements.txt on the target machine"
Write-Host "  Start  : pip install -r requirements.txt  then  启动画布服务器.bat"
Write-Host "  Port   : edit port.txt (default 10027)"
