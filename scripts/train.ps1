# Phase 5-6: model training/calibration/evaluation, scoring, BI star schema, Power BI snapshot.
. "$PSScriptRoot/_common.ps1"
Invoke-Step 'Train + calibrate + evaluate'  { Invoke-Cli @('train') }
Invoke-Step 'Score population'              { Invoke-Cli @('score') }
Invoke-Step 'BI mart (star schema)'         { Invoke-Cli @('build-mart') }
Invoke-Step 'Power BI CSV snapshot'         { Invoke-Cli @('export-bi') }
Invoke-Step 'Documentation (data dictionary, leakage review)' { Invoke-Cli @('docs') }
Invoke-Step 'Power BI project (PBIP) generation' { Invoke-Cli @('build-pbip') }
Invoke-Step 'PBIR schema validation'            { & $Python scripts/validate_pbir.py }
if (Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop -ErrorAction SilentlyContinue) {
    Invoke-Step 'TMDL parse check (Power BI Desktop serializer)' { & "$PSScriptRoot/validate_tmdl.ps1" }
}
