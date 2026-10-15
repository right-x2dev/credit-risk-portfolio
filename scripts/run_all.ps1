# One command from an empty database to scored mart + Power BI snapshot.
#   ./scripts/run_all.ps1            -> uses DATA_DIR from .env (default ./data/raw = Kaggle files)
#   ./scripts/run_all.ps1 -Synthetic -> generates synthetic look-alike data and runs on it (demo / CI)
param([switch]$Synthetic, [switch]$SkipTests)
. "$PSScriptRoot/_common.ps1"
$total = [Diagnostics.Stopwatch]::StartNew()
if ($Synthetic) {
    $env:DATA_DIR = './data/synthetic'
    & "$PSScriptRoot/bootstrap.ps1" -SkipInventory
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Invoke-Step 'Generate synthetic data' { Invoke-Cli @('make-synthetic', '--out', 'data/synthetic') }
    Invoke-Step 'Source file inventory' { Invoke-Cli @('inventory') }
} else {
    & "$PSScriptRoot/bootstrap.ps1"
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$PSScriptRoot/ingest.ps1";      if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$PSScriptRoot/build_marts.ps1"; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$PSScriptRoot/train.ps1";       if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $SkipTests) { & "$PSScriptRoot/test.ps1"; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
Write-Host ("All steps finished in {0:n1} min" -f $total.Elapsed.TotalMinutes) -ForegroundColor Green
exit 0
