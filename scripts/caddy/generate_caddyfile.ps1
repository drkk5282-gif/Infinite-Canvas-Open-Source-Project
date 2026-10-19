$ErrorActionPreference = "Stop"

$CaddyDir = $PSScriptRoot
$RootDir = Split-Path -Parent $CaddyDir
$PortFile = Join-Path $RootDir "port.txt"
$ExternalPortFile = Join-Path $RootDir "external_port.txt"
$DomainFile = Join-Path $CaddyDir "domain.txt"
$Template = Join-Path $CaddyDir "Caddyfile.template"
$Out = Join-Path $CaddyDir "Caddyfile"

$internalPort = 8088
if (Test-Path $PortFile) {
    $raw = (Get-Content $PortFile -Raw).Trim()
    if ($raw -match '^\d+$') { $internalPort = [int]$raw }
}

$externalPort = $internalPort
if (Test-Path $ExternalPortFile) {
    $raw = (Get-Content $ExternalPortFile -Raw).Trim()
    if ($raw -match '^\d+$') { $externalPort = [int]$raw }
}

$domain = "canvas.example.com"
if (Test-Path $DomainFile) {
    $domain = (Get-Content $DomainFile -Raw).Trim()
}
if (-not $domain) { throw "domain.txt is empty" }

if (-not (Test-Path $Template)) {
    throw "Caddyfile.template not found in $CaddyDir"
}

$text = (Get-Content $Template -Raw) `
    -replace '\{DOMAIN\}', $domain `
    -replace '\{INTERNAL_PORT\}', "$internalPort" `
    -replace '\{EXTERNAL_PORT\}', "$externalPort"
[System.IO.File]::WriteAllText($Out, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host "Caddyfile OK: https://${domain}:${externalPort} -> 127.0.0.1:${internalPort}"
