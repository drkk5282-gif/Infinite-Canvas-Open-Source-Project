param(
    [Parameter(Mandatory = $false)]
    [string]$InstallDir = "",
    [Parameter(Mandatory = $true)]
    [string]$UpdateZip
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $InstallDir) {
    $InstallDir = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path (Join-Path $InstallDir "CanvasServer.exe"))) {
        $InstallDir = $PSScriptRoot
    }
}

$InstallDir = (Resolve-Path $InstallDir).Path
$UpdateZip = (Resolve-Path $UpdateZip).Path

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if ($py) {
    & $py.Source (Join-Path $PSScriptRoot "apply_update.py") $InstallDir $UpdateZip
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($UpdateZip)
try {
    $entry = $zip.GetEntry("UPDATE.json")
    if (-not $entry) { throw "invalid update zip: UPDATE.json missing" }
    $reader = New-Object System.IO.StreamReader($entry.Open())
    $changelog = $reader.ReadToEnd() | ConvertFrom-Json
    $reader.Close()

    $extracted = 0
    foreach ($item in $zip.Entries) {
        if ($item.FullName -eq "UPDATE.json" -or $item.FullName.EndsWith("/")) { continue }
        $dest = Join-Path $InstallDir ($item.FullName -replace "/", "\")
        $parent = Split-Path -Parent $dest
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($item, $dest, $true)
        $extracted++
    }

    $removed = 0
    foreach ($rel in @($changelog.removed)) {
        $target = Join-Path $InstallDir ($rel -replace "/", "\")
        if (Test-Path $target) {
            Remove-Item -Force $target
            $removed++
        }
    }

    $failed = @()
    $hashes = $changelog.file_hashes
    if ($hashes) {
        $props = $hashes.PSObject.Properties
        foreach ($prop in $props) {
            $rel = $prop.Name
            $expected = [string]$prop.Value
            $target = Join-Path $InstallDir ($rel -replace "/", "\")
            if (-not (Test-Path $target)) {
                $failed += "$rel (missing)"
                continue
            }
            $actual = (Get-FileHash -Algorithm SHA256 -Path $target).Hash.ToLower()
            if ($actual -ne $expected.ToLower()) {
                $failed += "$rel (hash mismatch)"
            }
        }
    }

    Write-Host "product: $($changelog.product)"
    if ($changelog.version) {
        Write-Host "version: v$($changelog.version) build $($changelog.build)"
    }
    Write-Host "extracted: $extracted files"
    if ($removed) { Write-Host "removed: $removed files" }
    $exeUpdated = @($changelog.changed + $changelog.added) -contains "CanvasServer.exe"
    if ($exeUpdated) {
        Write-Host "CanvasServer.exe updated — restart required"
    } else {
        Write-Host "CanvasServer.exe not in this pack — restart still recommended"
    }

    if ($failed.Count) {
        Write-Host ""
        Write-Host "VERIFY FAILED:"
        foreach ($line in $failed) { Write-Host "  - $line" }
        exit 1
    }

    Write-Host ""
    Write-Host "VERIFY OK — all files match update manifest"
    Write-Host "Next: stop launcher, start again, then Ctrl+F5 in browser"
}
finally {
    $zip.Dispose()
}
