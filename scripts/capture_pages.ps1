# Render every report page in Power BI Desktop and save screenshots to powerbi/screenshots/.
# For each page: regenerate the PBIP opening on that page (CSV snapshot mode), open it, refresh the model through
# the local engine, capture the window. Requires an interactive (unlocked) Windows session for GPU rendering.
param([string[]]$Pages = @('p1_executive', 'p2_segmentation', 'p3_repayment', 'p4_model', 'p5_scenario'),
      [string]$OutDir = "$PSScriptRoot/../powerbi/screenshots")
. "$PSScriptRoot/_common.ps1"
New-Item -ItemType Directory -Force $OutDir | Out-Null
foreach ($page in $Pages) {
    & $Python -m credit_risk.cli build-pbip --data-source CSV --active-page $page | Out-Null
    & "$PSScriptRoot/open_check.ps1" -KeepOpen
    if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] $page did not open cleanly" -ForegroundColor Red; exit 1 }
    . "$PSScriptRoot/pbi_tools.ps1"
    $srv = Connect-PbiDesktop
    Invoke-PbiRefresh $srv
    $srv.Disconnect()
    Start-Sleep -Seconds 25
    & "$PSScriptRoot/capture_window.ps1" -Out (Join-Path (Resolve-Path $OutDir) "$page.png")
}
Get-Process PBIDesktop -ErrorAction SilentlyContinue | Stop-Process -Force
& $Python "$PSScriptRoot/crop_screenshots.py" (Resolve-Path $OutDir).Path
# leave the committed PBIP in its default state (PostgreSQL source, opens on page 1)
& $Python -m credit_risk.cli build-pbip | Out-Null
exit 0
