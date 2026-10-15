# Helpers to talk to the Analysis Services engine that Power BI Desktop runs locally while a report is open.
# Used for automated refresh + DAX-vs-SQL reconciliation (reports/powerbi_validation.md).
#   . ./scripts/pbi_tools.ps1
#   $srv = Connect-PbiDesktop
#   Invoke-PbiRefresh $srv
#   Invoke-Dax "EVALUATE ROW(""x"", [Total Customers])"
$ErrorActionPreference = 'Stop'

function Get-PbiBin {
    $pkg = Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop -ErrorAction SilentlyContinue
    if ($pkg) { return Join-Path $pkg.InstallLocation 'bin' }
    $msi = 'C:\Program Files\Microsoft Power BI Desktop\bin'
    if (Test-Path $msi) { return $msi }
    throw 'Power BI Desktop not found'
}

function Get-PbiPort {
    $roots = @("$env:USERPROFILE\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces",
               "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces")
    $f = $roots | Where-Object { Test-Path $_ } | ForEach-Object {
        Get-ChildItem $_ -Recurse -Filter msmdsrv.port.txt -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { throw 'No running Power BI Desktop model found (open the .pbip first)' }
    return [int]([IO.File]::ReadAllText($f.FullName, [Text.Encoding]::Unicode).Trim())
}

function Import-PbiAssemblies {
    $bin = Get-PbiBin
    foreach ($dll in 'Microsoft.PowerBI.Tabular.dll', 'Microsoft.PowerBI.AdomdClient.dll',
                     'Microsoft.AnalysisServices.Tabular.dll', 'Microsoft.AnalysisServices.AdomdClient.dll') {
        $p = Join-Path $bin $dll
        if (Test-Path $p) { [Reflection.Assembly]::LoadFrom($p) | Out-Null }
    }
}

function Connect-PbiDesktop {
    Import-PbiAssemblies
    $script:PbiPort = Get-PbiPort
    $srv = New-Object Microsoft.AnalysisServices.Tabular.Server
    $srv.Connect("Data Source=localhost:$script:PbiPort")
    return $srv
}

function Invoke-PbiRefresh($srv) {
    $db = $srv.Databases[0]
    $db.Model.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
    $db.Model.SaveChanges() | Out-Null
}

function Invoke-Dax([string]$Query) {
    $conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$script:PbiPort")
    $conn.Open()
    try {
        $cmd = $conn.CreateCommand(); $cmd.CommandText = $Query
        $reader = $cmd.ExecuteReader()
        $rows = @()
        while ($reader.Read()) {
            $o = [ordered]@{}
            for ($i = 0; $i -lt $reader.FieldCount; $i++) { $o[$reader.GetName($i)] = $reader.GetValue($i) }
            $rows += [pscustomobject]$o
        }
        $reader.Close()
        return $rows
    } finally { $conn.Close() }
}
