# Phase 2-4: staging aggregates, customer feature mart, EDA/statistics reports, model split.
. "$PSScriptRoot/_common.ps1"
Invoke-Step 'Staging (relational aggregation)' { Invoke-Cli @('build-staging') }
Invoke-Step 'Customer feature mart'           { Invoke-Cli @('build-features') }
Invoke-Step 'EDA + statistics reports'         { Invoke-Cli @('eda') }
Invoke-Step 'Train/validation/holdout split'   { Invoke-Cli @('split') }
