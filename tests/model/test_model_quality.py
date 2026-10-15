"""Model tests (spec §17.4) on the isolated synthetic pipeline run."""

from __future__ import annotations

import numpy as np
import pytest

from credit_risk import db


def frame(settings, q):
    with db.connect(settings) as conn:
        return db.read_frame(conn, q)


def test_split_ids_disjoint(built_pipeline):
    s = built_pipeline["settings"]
    d = frame(s, "SELECT split, count(*) n, count(DISTINCT sk_id_curr) u FROM analytics.model_split GROUP BY split")
    assert (d["n"] == d["u"]).all()
    total = frame(s, "SELECT count(*) n, count(DISTINCT sk_id_curr) u FROM analytics.model_split")
    assert total["n"].iloc[0] == total["u"].iloc[0] == 4000


def test_preprocessor_fit_on_train_only(built_pipeline):
    """Imputer medians of the LR pipeline must equal TRAIN medians (not train+validation+holdout)."""
    from credit_risk.features import select_features
    from credit_risk.train import build_lr_pipeline, load_model_frame

    s = built_pipeline["settings"]
    df = load_model_frame(s)
    num, cat = select_features(df.drop(columns=["split"]), s)
    tr = df[df["split"] == "train"]
    pipe = build_lr_pipeline(num[:5], [], s.model["models"]["logistic_regression"], 0)
    pipe.fit(tr[num[:5]], tr["target"])
    clipper = pipe.named_steps["pre"].named_transformers_["num"].named_steps["clip"]
    expected = np.nanquantile(tr[num[:5]].to_numpy(dtype=float), 0.99, axis=0)
    assert np.allclose(clipper.high_, expected, equal_nan=True)


def test_champion_beats_baseline_and_metrics_present(built_pipeline):
    summ = built_pipeline["summary"]
    vm = summ["validation_metrics_raw"]
    assert vm[summ["champion"]]["roc_auc"] > vm["baseline_mean"]["roc_auc"] + 0.01
    for metric in ("roc_auc", "pr_auc", "log_loss", "brier", "ks", "calibration_in_the_large"):
        assert metric in summ["holdout_calibrated"]


def test_holdout_metric_reproducible(built_pipeline):
    """Re-scoring holdout with the saved artifact reproduces the stored AUC within tolerance."""
    from sklearn.metrics import roc_auc_score

    from credit_risk.train import load_champion

    s = built_pipeline["settings"]
    model = load_champion(s)
    df = frame(s, "SELECT m.* FROM analytics.customer_feature_mart m JOIN analytics.model_split sp "
                  "USING (sk_id_curr) WHERE sp.split = 'holdout'")
    _, p = model.predict_pd(df)
    auc = roc_auc_score(df["target"], p)
    assert auc == pytest.approx(built_pipeline["summary"]["holdout_calibrated"]["roc_auc"], abs=1e-6)


def test_calibration_table_counts_match_sample(built_pipeline):
    s = built_pipeline["settings"]
    c = frame(s, "SELECT split, score_type, sum(customers) n FROM analytics.calibration_table GROUP BY 1, 2")
    sizes = built_pipeline["summary"]["split_sizes"]
    for _, r in c.iterrows():
        assert r["n"] == sizes[r["split"]]


def test_degradation_threshold_configured(project_settings):
    assert 0 < project_settings.model["evaluation"]["max_auc_drop_alert"] < 0.2


def test_artifact_reload_identical(built_pipeline):
    assert built_pipeline["summary"]["reload_max_abs_diff"] <= 1e-9
