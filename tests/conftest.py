"""Shared fixtures.

* Unit tests need no database.
* Integration / model tests run the real pipeline on a tiny synthetic dataset in a SEPARATE database
  (`<POSTGRES_DB>_test`) with reports/models/exports redirected to a temp dir, so they never touch the
  project database or the committed reports. They are skipped when PostgreSQL is not reachable.
* Data-quality tests inspect whatever the project database currently holds (skipped if mart is absent).
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from credit_risk.config import PROJECT_ROOT, load_settings


def _db_available(settings) -> bool:
    try:
        admin = settings.conninfo.replace(f"dbname={settings.pg_db}", "dbname=postgres")
        with psycopg.connect(admin, connect_timeout=3):
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def project_settings():
    return load_settings()


@pytest.fixture(scope="session")
def pipeline_env(tmp_path_factory):
    """Run the full pipeline once on tiny synthetic data in an isolated database."""
    base = load_settings()
    if not _db_available(base):
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")
    from credit_risk.synthetic import generate

    root = tmp_path_factory.mktemp("pipeline")
    data_dir = root / "data"
    generate(data_dir, n_train=4000, n_test=800, seed=11)
    overrides = {
        "POSTGRES_DB": f"{base.pg_db}_test",
        "DATA_DIR": str(data_dir),
        "MODEL_DIR": str(root / "models"),
        "REPORTS_DIR": str(root / "reports"),
        "POWERBI_EXPORT_DIR": str(root / "powerbi"),
    }
    settings = load_settings(overrides=overrides)
    (root / "reports" / "figures").mkdir(parents=True)

    from credit_risk import db

    db.ensure_database(settings)
    with db.connect(settings) as conn:
        conn.execute("DROP SCHEMA IF EXISTS raw, staging, analytics, mart, audit CASCADE")
        db.run_sql_dir(conn, PROJECT_ROOT / "sql" / "00_init")
    return {"settings": settings, "root": root, "data_dir": data_dir}


@pytest.fixture(scope="session")
def built_pipeline(pipeline_env):
    """Execute every pipeline step in order (except EDA, which has its own tests)."""
    from credit_risk import db
    from credit_risk.features import build_feature_mart
    from credit_risk.ingest import ingest_all
    from credit_risk.mart import build_mart, export_powerbi_snapshot
    from credit_risk.score import score_population
    from credit_risk.split import create_split
    from credit_risk.train import train_and_evaluate
    from credit_risk.transform import build_staging
    from credit_risk.utils import audited_run
    from credit_risk.validate import raw_checks

    s = pipeline_env["settings"]
    with db.connect(s) as conn, audited_run(conn, "ingest") as run_id:
        ingest_all(conn, s, run_id)
        raw_checks(conn, s, run_id).raise_on_fail()
    build_staging(s)
    build_feature_mart(s)
    create_split(s)
    summary = train_and_evaluate(s)
    score_population(s)
    build_mart(s)
    export_powerbi_snapshot(s)
    pipeline_env["summary"] = summary
    return pipeline_env


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in str(item.fspath) or "model" in Path(str(item.fspath)).parts:
            item.add_marker(pytest.mark.integration)


os.environ.setdefault("PYTHONHASHSEED", "0")
