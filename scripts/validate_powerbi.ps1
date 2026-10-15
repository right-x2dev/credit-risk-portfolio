# Headless Power BI validation (spec §17.5):
#   1. parse the generated TMDL with Power BI Desktop's TmdlSerializer
#   2. deploy it as a temporary database to the Analysis Services engine of a running Power BI Desktop
#   3. full refresh (CSV snapshot mode — no credentials needed)
#   4. run the DAX validation queries and write reports/powerbi_dax_results.json
# Prerequisite: Power BI Desktop is open (any file) so its local engine is running.
#   ./scripts/validate_powerbi.ps1 ; then: python -m credit_risk.cli powerbi-validation
param([string]$Folder = "$PSScriptRoot/../powerbi/Credit_Risk_Portfolio.SemanticModel/definition",
      [string]$Out = "$PSScriptRoot/../reports/powerbi_dax_results.json")
. "$PSScriptRoot/pbi_tools.ps1"
$srv = Connect-PbiDesktop
$name = 'CreditRiskValidation'
$db = [Microsoft.AnalysisServices.Tabular.TmdlSerializer]::DeserializeDatabaseFromFolder((Resolve-Path $Folder).Path)
# force CSV mode for the headless run
$ds = $db.Model.Expressions | Where-Object { $_.Name -eq 'DataSource' }
$ds.Expression = $ds.Expression -replace '^"PostgreSQL"', '"CSV"'
$existing = $srv.Databases.FindByName($name)
if ($existing) { $existing.Drop() }
$db.Name = $name; $db.ID = $name
$srv.Databases.Add($db)
$db.Update([Microsoft.AnalysisServices.UpdateOptions]::ExpandFull)
$sw = [Diagnostics.Stopwatch]::StartNew()
$db.Model.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
$db.Model.SaveChanges() | Out-Null
"refresh finished in {0:n1}s" -f $sw.Elapsed.TotalSeconds

$states = $db.Model.Tables | ForEach-Object { [pscustomobject]@{ table = $_.Name; state = "$($_.Partitions[0].State)";
    error = "$($_.Partitions[0].ErrorMessage)" } }
$bad = $states | Where-Object { $_.state -ne 'Ready' }
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$PbiPort;Catalog=$name")
$conn.Open()
function Q([string]$dax) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $dax
    $r = $cmd.ExecuteReader(); $rows = @()
    while ($r.Read()) { $o = [ordered]@{}; for ($i = 0; $i -lt $r.FieldCount; $i++) {
        $o[$r.GetName($i) -replace '^\[|\]$', ''] = $r.GetValue($i) }; $rows += [pscustomobject]$o }
    $r.Close(); return , $rows
}
$results = [ordered]@{}
$results.table_states = $states
$results.portfolio = Q @"
EVALUATE ROW(
 "total_customers", [Total Customers], "total_exposure", [Total Exposure],
 "observed_difficulty_rate", [Observed Difficulty Rate], "average_pd", [Average PD],
 "exposure_weighted_pd", [Exposure Weighted PD], "base_expected_loss", [Base Expected Loss], "el_rate", [EL Rate],
 "high_risk_customer_pct", [High Risk Customer %], "high_risk_exposure_pct", [High Risk Exposure %],
 "risk_capture_exposure", [Risk Capture (Exposure)], "past_due_customer_pct", [Past Due Customer %],
 "severe_delinquency_customer_pct", [Severe Delinquency Customer %], "average_payment_ratio", [Average Payment Ratio],
 "average_credit_utilization", [Average Credit Utilization],
 "scenario_el_default", [Scenario Expected Loss], "scenario_change_default", [Scenario EL Change])
"@
$results.by_grade = Q @"
EVALUATE SUMMARIZECOLUMNS(dim_risk_grade[risk_grade], dim_risk_grade[sort_key],
 "customers", [Total Customers], "exposure", [Total Exposure], "base_expected_loss", [Base Expected Loss],
 "average_pd", [Average PD], "observed_difficulty_rate", [Observed Difficulty Rate])
"@
$results.grade_sum_check = Q @"
EVALUATE ROW("sum_of_grades_el", SUMX(VALUES(dim_risk_grade[risk_grade]), [Base Expected Loss]), "total_el", [Base Expected Loss],
 "sum_of_grades_customers", SUMX(VALUES(dim_risk_grade[risk_grade]), [Total Customers]))
"@
$results.filtered_spot_check = Q @"
EVALUATE CALCULATETABLE(ROW("customers", [Total Customers], "exposure", [Total Exposure], "ew_pd", [Exposure Weighted PD],
 "el", [Base Expected Loss]), dim_customer_segment[income_band] = "100K-150K", dim_customer_segment[gender] = "Female")
"@
$results.scenario_stress = Q @"
EVALUATE CALCULATETABLE(ROW("scenario_el", [Scenario Expected Loss], "bridge_total",
 SUMX(VALUES('EL Bridge'[Step Order]), [EL Bridge Value])),
 TREATAS({0.2}, 'PD Stress'[PD Stress Value]), TREATAS({0.6}, 'LGD Assumption'[LGD Assumption Value]),
 TREATAS({-0.1}, 'Exposure Change'[Exposure Change Value]))
"@
$results.sensitivity = Q @"
EVALUATE SUMMARIZECOLUMNS('Sensitivity PD'[PD Stress Level], 'Sensitivity LGD'[LGD Level], "el", [Sensitivity EL])
"@
$results.model_holdout = Q @"
EVALUATE CALCULATETABLE(ROW("roc_auc", [ROC-AUC], "pr_auc", [PR-AUC], "brier", [Brier Score], "ks", [KS],
 "threshold", [Selected Threshold], "recall", [Recall at Threshold], "precision", [Precision at Threshold]),
 dim_split[dataset_split] = "holdout")
"@
$results.narratives = Q @"
EVALUATE ROW("executive", [Executive Narrative], "model", CALCULATE([Model Summary Narrative], dim_split[dataset_split] = "holdout"),
 "header", [Header Info])
"@
$conn.Close()
$results | ConvertTo-Json -Depth 6 | Out-File -FilePath $Out -Encoding utf8
$db.Drop()
"wrote $Out"
if ($bad) { $bad | Format-Table -AutoSize | Out-String -Width 300; exit 1 }
exit 0
