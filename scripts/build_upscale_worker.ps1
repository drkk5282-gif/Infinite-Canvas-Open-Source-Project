# Build UpscaleWorker GUI (run from repo root on Windows)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venv = Join-Path $Root ".venv"
if (-not (Test-Path $venv)) {
    python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install -q pyinstaller httpx

$dist = Join-Path $Root "dist\QingZiUpscale"
$workerDir = Join-Path $Root "upscale-worker"

& $py -m PyInstaller `
    --noconfirm `
    --clean `
    UpscaleWorker.spec

$target = Join-Path $dist "QingZiUpscale.exe"
if (-not (Test-Path $target)) {
    Write-Error "Build failed: $target not found"
}
Copy-Item -Force "$workerDir\config.ini.example" "$dist\config.ini.example"
Copy-Item -Force "$workerDir\start.vbs" "$dist\start.vbs"
Write-Host ""
Write-Host "Built: $target"
Write-Host "Folder: $dist"
Write-Host "1) Copy tools\engines\realesrgan-ncnn-vulkan into dist\QingZiUpscale\"
Write-Host "2) Run QingZiUpscale.exe, paste admin link, set threads, Start"
