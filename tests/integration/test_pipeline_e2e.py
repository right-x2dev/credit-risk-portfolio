"""End-to-end: fixture CSV -> raw -> staging -> feature mart -> split -> model -> scoring -> BI mart (spec §17.3)."""

from __future__ import annotations

import shutil

import pytest

from credit_risk import db
from credit_risk.calibrate import FeatureSchemaError
from credit_risk.ingest import IngestError, ingest_all
from credit_risk.utils import audited_run


def scalar(settings, query):
    with db.connect(settings) as conn:
        return db.scalar(conn, query)


def test_full_flow_population_reconciles(built_pipeline):
    s = built_pipeline["settings"]
    assert scalar(s, "SELECT count(*) FROM raw.application_train") == 4000
    assert scalar(s, "SELECT count(*) FROM analytics.customer_feature_mart") == 4800
    assert scalar(s, "SELECT count(*) FROM mart.fact_customer_risk") == 4000
    assert scalar(s, "SELECT count(*) FROM analytics.model_predictions") == 4800
    assert scalar(s, "SELECT count(*) FROM audit.data_quality_result WHERE status = 'fail'") == 0


def test_rerun_ingest_does_not_duplicate(built_pipeline):
    s = built_pipeline["settings"]
    before = scalar(s, "SELECT count(*) FROM raw.bureau")
    with db.connect(s) as conn, audited_run(conn, "ingest") as run_id:
        results = ingest_all(conn, s, run_id)
    assert all(r["status"] == "skipped_unchanged" for r in results)
    with db.connect(s) as conn, audited_run(conn, "ingest") as run_id:
        ingest_all(conn, s, run_id, force=True)
    assert scalar(s, "SELECT count(*) FROM raw.bureau") == before


def test_failed_run_is_audited(built_pipeline, tmp_path):
    from dataclasses import replace

    s = built_pipeline["settings"]
    broken_dir = tmp_path / "broken"
    shutil.copytree(built_pipeline["data_dir"], broken_dir)
    (broken_dir / "bureau.csv").unlink()
    broken = replace(s, data_dir=broken_dir)
    with pytest.raises(IngestError):
        with db.connect(broken) as conn, audited_run(conn, "ingest", str(broken_dir)) as run_id:
            ingest_all(conn, broken, run_id)
    status = scalar(s, "SELECT status FROM audit.pipeline_run ORDER BY run_id DESC LIMIT 1")
    assert status == "failed"


def test_feature_schema_mismatch_is_explicit(built_pipeline):
    from credit_risk.train import load_champion

    model = load_champion(built_pipeline["settings"])
    with db.connect(built_pipeline["settings"]) as conn:
        df = db.read_frame(conn, "SELECT * FROM analytics.customer_feature_mart LIMIT 50")
    with pytest.raises(FeatureSchemaError):
        model.predict_pd(df.drop(columns=[model.features[0]]))


def test_powerbi_snapshot_matches_mart(built_pipeline):
    import json

    s = built_pipeline["settings"]
    manifest = json.loads((s.export_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["fact_customer_risk"]["rows"] == 4000
    assert manifest["tables"]["dim_risk_grade"]["rows"] == len(s.project["risk"]["grades"])
