# Phase 0: Python env + PostgreSQL container + schemas. Re-runnable.
param([switch]$SkipInventory)
. "$PSScriptRoot/_common.ps1"

Invoke-Step 'Python 3.12 virtual environment' {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv is required: https://docs.astral.sh/uv/' }
    if (-not (Test-Path "$ProjectRoot/.venv")) { uv venv --python 3.12 .venv }
    uv pip install --python .venv -r requirements.lock
    uv pip install --python .venv -e . --no-deps
}
$Python = Join-Path $ProjectRoot '.venv/Scripts/python.exe'

Invoke-Step 'PostgreSQL container (docker compose)' {
    docker compose up -d --wait postgres
}

Invoke-Step 'Database schemas + audit tables' { Invoke-Cli @('init-db') }
if (-not $SkipInventory) { Invoke-Step 'Source file inventory' { Invoke-Cli @('inventory') } }
