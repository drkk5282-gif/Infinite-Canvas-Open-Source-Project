# Build portable Windows folder: dist/CanvasServer/

$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$OutputEncoding = [System.Text.Encoding]::UTF8

chcp 65001 | Out-Null

$Root = Split-Path -Parent $PSScriptRoot

Set-Location $Root



Write-Host "==> Root: $Root"

Write-Host "==> Profile: Canvas-only server (no browser_channels, no Chromium pack)"



$pyCmd = Get-Command python -ErrorAction SilentlyContinue

if (-not $pyCmd) { $pyCmd = Get-Command py -ErrorAction SilentlyContinue }

if (-not $pyCmd) { throw "Python not found. Install Python 3.10+ first." }

$py = $pyCmd.Source

Write-Host "==> Python: $py"



Write-Host "==> Installing build deps..."

& $py -m pip install -q -r requirements.txt pyinstaller



Write-Host "==> Ensuring video upscale engine (optional)..."

& $py (Join-Path $Root "scripts\setup_upscale_engine.py")

$engineExe = Join-Path $Root "tools\engines\realesrgan-ncnn-vulkan\realesrgan-ncnn-vulkan.exe"

if (-not (Test-Path $engineExe)) {

  Write-Warning "Upscale engine missing at $engineExe (AI upscale unavailable)"

}



Write-Host "==> PyInstaller build (CanvasServer.exe)..."

Remove-Item -Recurse -Force (Join-Path $Root "build") -ErrorAction SilentlyContinue

Remove-Item -Recurse -Force (Join-Path $Root "dist\CanvasServer") -ErrorAction SilentlyContinue

& $py -m PyInstaller --noconfirm --clean (Join-Path $Root "CanvasServer.spec")



$Out = Join-Path $Root "dist\CanvasServer"

if (-not (Test-Path (Join-Path $Out "CanvasServer.exe"))) {

    throw "Build failed: CanvasServer.exe missing in $Out"

}



Write-Host "==> Bump version..."

$versionJson = & $py (Join-Path $Root "scripts\bump_version.py") "CanvasServer"

$version = $versionJson | ConvertFrom-Json

Write-Host "==> Version: v$($version.version) build $($version.build)"



Write-Host "==> Bump static cache (?v=b$($version.build))..."

& $py (Join-Path $Root "scripts\bump_static_cache.py")



$feSrc = Join-Path $Root "frontend"

$feDst = Join-Path $Out "frontend"

if (Test-Path $feDst) { Remove-Item -Recurse -Force $feDst }

Copy-Item -Recurse $feSrc $feDst



$adminSrc = Join-Path $Root "admin"

$adminDst = Join-Path $Out "admin"

if (Test-Path $adminSrc) {

  if (Test-Path $adminDst) { Remove-Item -Recurse -Force $adminDst }

  Copy-Item -Recurse $adminSrc $adminDst

  Write-Host "==> Copied admin/"

}



Write-Host "==> Sanitize UI (MyApi / public models only)..."

& $py (Join-Path $Root "scripts\sanitize_api_only_ui.py") $Out



$profile = @{

  disable_browser_channels = $true

  disable_doubao = $true

  disable_watermark = $true

  product_label = "Canvas Server"

  skip_playwright_pack = $true

  version = $version.version

  build = [int]$version.build

  built_at = $version.built_at

} | ConvertTo-Json -Compress

$profilePath = Join-Path $Out "build_profile.json"

[System.IO.File]::WriteAllText($profilePath, $profile, [System.Text.UTF8Encoding]::new($false))

Write-Host "==> Wrote build_profile.json"



Copy-Item -Force (Join-Path $Root "version.json") (Join-Path $Out "version.json")

Write-Host "==> Copied version.json"



$data = Join-Path $Out "data"

& $py (Join-Path $Root "scripts\init_pack_data.py") $data server



$toolsEnginesSrc = Join-Path $Root "tools\engines"

$toolsEnginesDst = Join-Path $Out "tools\engines"

if (Test-Path $toolsEnginesSrc) {

  Write-Host "==> Copying tools/engines only (no probe/share scripts)..."

  if (Test-Path (Join-Path $Out "tools")) { Remove-Item -Recurse -Force (Join-Path $Out "tools") }

  New-Item -ItemType Directory -Force -Path (Join-Path $Out "tools") | Out-Null

  Copy-Item -Recurse $toolsEnginesSrc $toolsEnginesDst

} else {

  Write-Warning "tools/engines missing — AI upscale unavailable in pack"

}



$engineDir = Join-Path $Out "tools\engines\realesrgan-ncnn-vulkan"

$vulkanDst = Join-Path $engineDir "vulkan-1.dll"

$vulkanSrc = Join-Path $env:SystemRoot "System32\vulkan-1.dll"

if ((Test-Path $engineDir) -and (-not (Test-Path $vulkanDst)) -and (Test-Path $vulkanSrc)) {

  Copy-Item -Force $vulkanSrc $vulkanDst

  Write-Host "==> Bundled vulkan-1.dll into upscale engine"

} elseif ((Test-Path $engineDir) -and (-not (Test-Path $vulkanDst))) {

  Write-Warning "vulkan-1.dll not found on this PC. Target server needs GPU driver / Vulkan Runtime."

}



Copy-Item -Force (Join-Path $Root "requirements.txt") (Join-Path $Out "requirements.txt")

Copy-Item -Force (Join-Path $Root "run.py") (Join-Path $Out "run.py")

Copy-Item -Recurse -Force (Join-Path $Root "backend") (Join-Path $Out "backend")

# Optional parser is not part of the public pack.
$zacaoPy = Join-Path $Out "backend\zacao_parser.py"
if (Test-Path $zacaoPy) {
  Remove-Item -Force $zacaoPy
  Write-Host "==> Removed backend/zacao_parser.py from pack"
}


& $py (Join-Path $Root "scripts\stage_pack_extras.py") $Out server



$zip = Join-Path $Root "dist\CanvasServer-portable.zip"

Write-Host "==> Zipping to $zip (data/ excluded — safe server deploy)..."

& $py (Join-Path $Root "scripts\zip_release.py") $Out $zip --exclude-data



Write-Host "==> Building update pack (diff vs previous release)..."

& $py (Join-Path $Root "scripts\release_pack.py") finalize $Out --product CanvasServer --full-zip $zip



$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)

$updateZip = Get-ChildItem -Path (Join-Path $Root "dist") -Filter "CanvasServer-update-*.zip" |

  Sort-Object LastWriteTime -Descending |

  Select-Object -First 1

Write-Host ""

Write-Host "DONE (Canvas-only server — MyApi HTTP channels)."

Write-Host "  Folder : $Out"

Write-Host "  Zip    : $zip  ($size MB)"

if ($updateZip) {

  $uSize = [math]::Round($updateZip.Length / 1MB, 2)

  Write-Host "  Update : $($updateZip.FullName)  ($uSize MB)"

} else {

  Write-Host "  Update : (none — first build or no file changes)"

}

Write-Host "  Start  : 启动画布服务器.bat + 启动任务服务.bat (CanvasServer.exe)"

Write-Host "  Port   : edit port.txt (default 10027)"

