# Phase 1: raw load (COPY) + raw data-quality checks. Unchanged files are skipped; -Force reloads.
param([switch]$Force)
. "$PSScriptRoot/_common.ps1"
$cliArgs = @('ingest'); if ($Force) { $cliArgs += '--force' }
Invoke-Step 'Raw ingestion + quality' { Invoke-Cli $cliArgs }
