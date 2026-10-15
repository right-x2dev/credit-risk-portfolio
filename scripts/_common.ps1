# Shared helpers for the pipeline scripts. Dot-source: . "$PSScriptRoot/_common.ps1"
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path "$ProjectRoot/.env")) {
    Copy-Item "$ProjectRoot/.env.example" "$ProjectRoot/.env"
    Write-Host "[info] .env created from .env.example - change POSTGRES_PASSWORD for anything beyond a local demo"
}

$Python = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $Python)) { $Python = Join-Path $ProjectRoot '.venv/bin/python' }

function Invoke-Step {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][scriptblock]$Block)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "==> $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Block
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host ("[ OK ] {0} ({1:n1}s)" -f $Name, $sw.Elapsed.TotalSeconds) -ForegroundColor Green
}

function Invoke-Cli {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & $Python -m credit_risk.cli @Arguments
}
