"""Unit tests for business rules (spec §17.1)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from credit_risk.calibrate import (
    FeatureSchemaError,
    IsotonicCalibrator,
    PDModel,
    SigmoidCalibrator,
    schema_hash,
)
from credit_risk.config import ConfigError, load_settings, load_yaml
from credit_risk.evaluate import calibration_table, core_metrics, ks_statistic, lift_table, wilson_interval
from credit_risk.features import LeakageError, assert_no_leakage, is_excluded, select_features
from credit_risk.ingest import find_file, normalise_name
from credit_risk.risk import assign_grade, clip_pd, expected_loss, scenario_expected_loss
from credit_risk.split import SplitError, assert_disjoint, make_split


@pytest.fixture(scope="module")
def settings():
    return load_settings()


GRADES = [
    {"grade": "Low", "lower": 0.0, "upper": 0.05},
    {"grade": "Medium", "lower": 0.05, "upper": 0.10},
    {"grade": "High", "lower": 0.10, "upper": 0.20},
    {"grade": "Critical", "lower": 0.20, "upper": 1.0001},
]


# --------------------------------------------------------------------- config
def test_config_loads_required_sections(settings):
    assert settings.project["risk"]["base_lgd"] == pytest.approx(0.45)
    assert set(settings.model["split"]) >= {"train", "validation", "holdout"}
    assert sum(settings.model["split"][k] for k in ("train", "validation", "holdout")) == pytest.approx(1.0)


def test_config_missing_key_raises(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_yaml(p, ("a", "b"))
    with pytest.raises(ConfigError):
        load_yaml(tmp_path / "missing.yaml")


def test_risk_grades_are_contiguous(settings):
    grades = settings.project["risk"]["grades"]
    assert grades[0]["lower"] == 0
    for a, b in zip(grades, grades[1:], strict=False):
        assert a["upper"] == b["lower"], "grade boundaries must not overlap or leave gaps"
    assert grades[-1]["upper"] > 1


# --------------------------------------------------------------------- ingestion helpers
def test_find_file_is_case_insensitive(tmp_path):
    (tmp_path / "pos_cash_BALANCE.csv").write_text("a\n1\n")
    assert find_file(tmp_path, "POS_CASH_balance.csv").name == "pos_cash_BALANCE.csv"
    assert find_file(tmp_path, "missing.csv") is None


def test_normalise_name():
    assert normalise_name("SK_ID_CURR", 1) == "sk_id_curr"
    assert normalise_name("", 0) == "row_idx"
    assert normalise_name("Unnamed: 3", 3) == "col_3"


# --------------------------------------------------------------------- sentinel / ratio rules (python mirrors of SQL)
def test_days_employed_sentinel_cleaning():
    s = pd.Series([-100, 365243, None])
    clean = s.where(s != 365243)
    assert clean.isna().tolist() == [False, True, True]


def test_ratio_zero_denominator_is_null():
    num, den = np.array([1.0, 2.0]), np.array([0.0, 4.0])
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(den == 0, np.nan, num / np.where(den == 0, 1, den))
    assert np.isnan(out[0]) and out[1] == 0.5


def test_dpd_and_payment_ratio_definitions():
    days_instalment, entry = np.array([-30, -30, -30]), np.array([-35, -30, -20])
    dpd = np.maximum(entry - days_instalment, 0)
    dbd = np.maximum(days_instalment - entry, 0)
    assert dpd.tolist() == [0, 0, 10] and dbd.tolist() == [5, 0, 0]
    assert (np.array([50.0]) / np.array([100.0]))[0] == 0.5


# --------------------------------------------------------------------- risk
@pytest.mark.parametrize("pd_value,grade", [(0.0, "Low"), (0.0499, "Low"), (0.05, "Medium"), (0.0999, "Medium"),
                                            (0.10, "High"), (0.1999, "High"), (0.20, "Critical"), (1.0, "Critical")])
def test_grade_boundaries(pd_value, grade):
    assert assign_grade(np.array([pd_value]), GRADES)[0] == grade


def test_grade_outside_range_raises():
    with pytest.raises(ValueError):
        assign_grade(np.array([1.5]), GRADES)


def test_pd_clipping():
    assert clip_pd(np.array([-0.1, 0.5, 1.2]), 1e-4, 1 - 1e-4).tolist() == [1e-4, 0.5, 1 - 1e-4]


def test_expected_loss_and_scenario():
    p, e = np.array([0.1, 0.5]), np.array([1000.0, 2000.0])
    el = expected_loss(p, e, 0.45)
    assert el.tolist() == pytest.approx([45.0, 450.0])
    assert scenario_expected_loss(p, e, 0.45) == pytest.approx(el.sum())          # defaults == base
    # +100% PD stress caps the 0.5 PD at 1.0
    assert scenario_expected_loss(p, e, 0.45, pd_stress=1.0) == pytest.approx(0.2 * 1000 * 0.45 + 1.0 * 2000 * 0.45)
    assert scenario_expected_loss(p, e, 0.45, exposure_change=-0.3) == pytest.approx(el.sum() * 0.7)
    with pytest.raises(ValueError):
        expected_loss(np.array([1.2]), e[:1], 0.45)
    with pytest.raises(ValueError):
        expected_loss(p[:1], np.array([-1.0]), 0.45)


# --------------------------------------------------------------------- features / leakage
def test_feature_policy_excludes_label_and_ids(settings):
    for col in ("target", "sk_id_curr", "source", "predicted_pd_x", "days_employed"):
        assert is_excluded(col, settings)
    assert not is_excluded("ext_source_2", settings)
    df = pd.DataFrame({"sk_id_curr": [1, 2], "target": [0, 1], "a": [1.0, 2.0], "b": ["x", "y"], "empty": [np.nan] * 2})
    num, cat = select_features(df, settings)
    assert num == ["a"] and cat == ["b"]


def test_leakage_guard_blocks_label_columns(settings):
    with pytest.raises(LeakageError):
        assert_no_leakage(["ext_source_1", "target"], settings)
    with pytest.raises(LeakageError):
        assert_no_leakage(["my_target_mean"], settings)


def test_leakage_guard_detects_label_proxy(settings):
    rng = np.random.default_rng(0)
    y = pd.Series(rng.integers(0, 2, 1000))
    X = pd.DataFrame({"leaky": y * 10 + rng.normal(0, 0.01, 1000), "noise": rng.normal(size=1000)})
    with pytest.raises(LeakageError):
        assert_no_leakage(["leaky", "noise"], settings, X, y)
    report = assert_no_leakage(["noise"], settings, X[["noise"]], y)
    assert report["max_single_feature_auc"] < 0.6


# --------------------------------------------------------------------- split
def test_split_is_disjoint_stratified_and_reproducible():
    rng = np.random.default_rng(1)
    ids = np.arange(10_000)
    y = (rng.random(10_000) < 0.08).astype(int)
    a = make_split(ids, y, 0.7, 0.15, 0.15, seed=42)
    b = make_split(ids, y, 0.7, 0.15, 0.15, seed=42)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 10_000 and a["sk_id_curr"].is_unique
    rates = a.assign(y=y[a["sk_id_curr"]]).groupby("split")["y"].mean()
    assert rates.max() - rates.min() < 0.01
    assert a["split"].value_counts(normalize=True)["train"] == pytest.approx(0.7, abs=0.001)


def test_split_rejects_duplicates_and_overlap():
    with pytest.raises(SplitError):
        make_split(np.array([1, 1, 2, 3]), np.array([0, 1, 0, 1]), 0.5, 0.25, 0.25, 0)
    bad = pd.DataFrame({"sk_id_curr": [1, 1], "split": ["train", "holdout"]})
    with pytest.raises(SplitError):
        assert_disjoint(bad)


# --------------------------------------------------------------------- metrics
def test_ks_and_wilson():
    y = np.array([0, 0, 1, 1])
    assert ks_statistic(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)
    lo, hi = wilson_interval(10, 100)
    assert lo == pytest.approx(0.0552, abs=1e-3) and hi == pytest.approx(0.1744, abs=1e-3)


def test_calibration_and_lift_tables_sum_to_sample():
    rng = np.random.default_rng(3)
    p = rng.random(5000) * 0.3
    y = (rng.random(5000) < p).astype(int)
    ct = calibration_table(y, p, np.ones(5000), bins=10)
    assert ct["customers"].sum() == 5000 and ct["positives"].sum() == y.sum()
    lt = lift_table(y, p, np.ones(5000))
    assert lt["customers"].sum() == 5000
    assert lt["cum_capture_rate"].iloc[-1] == pytest.approx(1.0)
    m = core_metrics(y, p, np.ones(5000), threshold=0.2)
    assert 0.5 < m["roc_auc"] <= 1 and m["tp_at_threshold"] + m["fn_at_threshold"] == y.sum()


# --------------------------------------------------------------------- calibration / model wrapper
def test_calibrators_are_monotone_and_bounded():
    rng = np.random.default_rng(4)
    raw = rng.random(4000)
    y = (rng.random(4000) < raw * 0.5).astype(int)
    grid = np.linspace(0, 1, 50)
    for cal in (SigmoidCalibrator().fit(raw, y), IsotonicCalibrator().fit(raw, y)):
        out = cal.transform(grid)
        assert np.all(np.diff(out) >= -1e-12) and out.min() >= 0 and out.max() <= 1


def test_pd_model_schema_mismatch_raises():
    model = PDModel("v", "lightgbm", pipeline=None, calibrator=None, numeric_features=["a", "b"],
                    categorical_features=[])
    with pytest.raises(FeatureSchemaError):
        model.predict_pd(pd.DataFrame({"a": [1.0]}))
    assert schema_hash(["a"], ["b"]) != schema_hash(["b"], ["a"])
