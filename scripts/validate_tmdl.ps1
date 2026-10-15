# Parse the generated TMDL with Power BI Desktop's own TmdlSerializer (no UI needed). Exit 1 on error.
param([string]$Folder = "$PSScriptRoot/../powerbi/Credit_Risk_Portfolio.SemanticModel/definition")
. "$PSScriptRoot/pbi_tools.ps1"
Import-PbiAssemblies
try {
    $db = [Microsoft.AnalysisServices.Tabular.TmdlSerializer]::DeserializeDatabaseFromFolder((Resolve-Path $Folder).Path)
    "TMDL OK: tables=$($db.Model.Tables.Count) relationships=$($db.Model.Relationships.Count) expressions=$($db.Model.Expressions.Count) measures=$(($db.Model.Tables | ForEach-Object { $_.Measures.Count } | Measure-Object -Sum).Sum)"
} catch {
    $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }
    Write-Host "TMDL ERROR: $($e.Message)" -ForegroundColor Red
    exit 1
}
exit 0
