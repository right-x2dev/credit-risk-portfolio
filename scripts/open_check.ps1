# Open the PBIP in Power BI Desktop and report whether it loads cleanly (no "problem found" dialog).
# Works headless (UI Automation), e.g. while the screen is locked.  Exit 0 = OK, 1 = error dialog / timeout.
param([string]$Pbip = "$PSScriptRoot/../powerbi/Credit_Risk_Portfolio.pbip", [int]$TimeoutSec = 180, [switch]$KeepOpen)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Get-Process PBIDesktop -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
Start-Process (Resolve-Path $Pbip).Path
$root = [System.Windows.Automation.AutomationElement]::RootElement
$all = [System.Windows.Automation.Condition]::TrueCondition
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$status = 'timeout'
$stem = [IO.Path]::GetFileNameWithoutExtension($Pbip)
# Korean UI text written as escapes so Windows PowerShell 5.1 (ANSI script parsing) handles it
$errPattern = (-join [char[]](0xBB38, 0xC81C)) + '|' + (-join [char[]](0xC624, 0xB958)) + '|problem|error|Error|Couldn'
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $ids = (Get-Process PBIDesktop -ErrorAction SilentlyContinue).Id
    if (-not $ids) { continue }
    $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $all) | Where-Object { $ids -contains $_.Current.ProcessId }
    $names = @()
    foreach ($w in $wins) {
        $names += $w.Current.Name
        $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Window))) | ForEach-Object { $names += $_.Current.Name }
    }
    $joined = $names -join ' | '
    if ($joined -match $errPattern) { $status = "error: $joined"; break }
    if ($joined -match [regex]::Escape($stem)) { Start-Sleep -Seconds 20
        $again = ($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Window))) | Where-Object { $ids -contains $_.Current.ProcessId } |
            ForEach-Object { $_.Current.Name }) -join ' | '
        if ($again -match $errPattern) { $status = "error: $again" } else { $status = "ok: $again" }
        break }
}
$status
if (-not $KeepOpen) { Get-Process PBIDesktop -ErrorAction SilentlyContinue | Stop-Process -Force }
if ($status -like 'ok*') { exit 0 } else { exit 1 }
