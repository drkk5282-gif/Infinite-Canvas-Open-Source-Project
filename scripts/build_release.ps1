# Default release: CanvasServer-py (MyApi). Browser simulation pack removed.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "========================================"
Write-Host "  CanvasServer-py (MyApi)"
Write-Host "========================================"
& (Join-Path $PSScriptRoot "build_release_python.ps1") @args
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
  throw "CanvasServer-py pack failed"
}

Write-Host ""
Write-Host "========================================"
Write-Host "  PACK DONE"
Write-Host "========================================"
Write-Host "  Zip    : dist\CanvasServer-py-portable.zip"
Write-Host "  Folder : dist\CanvasServer-py\"
