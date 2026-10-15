# Full Power BI verification on this machine (needs Power BI Desktop; screenshots need an unlocked session):
#   TMDL parse -> open PBIP (no error dialog) -> deploy+refresh+DAX checks -> page screenshots -> validation report
param([switch]$SkipScreenshots)
. "$PSScriptRoot/_common.ps1"
Invoke-Step 'TMDL parse'           { & "$PSScriptRoot/validate_tmdl.ps1" }
Invoke-Step 'PBIR schema'          { & $Python "$PSScriptRoot/validate_pbir.py" }
Invoke-Step 'Open PBIP in Desktop' { & "$PSScriptRoot/open_check.ps1" -KeepOpen }
Invoke-Step 'Deploy + refresh + DAX checks' { & "$PSScriptRoot/validate_powerbi.ps1" }
if (-not $SkipScreenshots) { Invoke-Step 'Page screenshots' { & "$PSScriptRoot/capture_pages.ps1" } }
Get-Process PBIDesktop -ErrorAction SilentlyContinue | Stop-Process -Force
Invoke-Step 'DAX vs SQL reconciliation report' { Invoke-Cli @('powerbi-validation') }
