# Lint + unit tests + integration/data-quality/model tests (integration needs PostgreSQL running).
param([switch]$UnitOnly)
. "$PSScriptRoot/_common.ps1"
Invoke-Step 'ruff lint' { & $Python -m ruff check src tests scripts }
if ($UnitOnly) {
    Invoke-Step 'unit tests' { & $Python -m pytest tests/unit -q }
} else {
    Invoke-Step 'all tests' { & $Python -m pytest tests -q }
}
