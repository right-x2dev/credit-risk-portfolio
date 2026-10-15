"""Customer feature mart (Phase 4), feature registry and model feature policy.

The SQL in sql/03_analytics builds the 1-row-per-customer mart. This module
* writes config-driven lookup tables (risk grades, segment bands) so SQL never hard-codes thresholds,
* verifies the mart grain and population,
* maintains analytics.feature_registry (source, type, null ratio, leakage risk, model usage),
* decides which columns the model may use and enforces it (`assert_no_leakage`).
"""

from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd
import psycopg

from .config import Settings
from .db import connect, run_sql_dir, write_frame
from .transform import sql_params
from .utils import audited_run, timed
from .validate import QualityRunner

log = logging.getLogger(__name__)

SOURCE_PREFIXES = {
    "bur_": "bureau.csv + bureau_balance.csv",
    "prev_": "previous_application.csv",
    "inst_": "installments_payments.csv",
    "pos_": "POS_CASH_balance.csv",
    "cc_": "credit_card_balance.csv",
    "has_": "derived (history availability flag)",
}
DERIVED_APP_COLUMNS = {
    "age_years", "employment_years", "employment_age_ratio", "credit_income_ratio", "annuity_income_ratio",
    "credit_annuity_ratio", "goods_credit_ratio", "credit_minus_goods", "income_per_person",
    "days_id_publish_years", "days_registration_years", "phone_change_years", "document_count",
    "contactability_count", "ext_source_mean", "ext_source_min", "ext_source_max", "ext_source_std",
    "ext_source_missing_count", "ext_source_1_missing", "ext_source_3_missing", "bureau_enquiry_missing",
    "days_employed_anom", "days_employed_clean",
}


class LeakageError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- config tables
def write_config_tables(conn: psycopg.Connection, settings: Settings) -> None:
    risk = settings.project["risk"]
    grades = pd.DataFrame(risk["grades"]).rename(columns={"lower": "pd_lower", "upper": "pd_upper"})
    grades["risk_group"] = grades["grade"]
    grades["is_high_risk"] = grades["grade"].isin(risk["high_risk_grades"])
    grades["label"] = grades.apply(
        lambda r: f"{r['grade']} ({r['pd_lower']:.0%}–{min(r['pd_upper'], 1):.0%})", axis=1)
    write_frame(conn, grades[["grade", "risk_group", "label", "pd_lower", "pd_upper", "sort_key", "color_hex",
                              "is_high_risk"]], "analytics.cfg_risk_grade", primary_key=["grade"])

    seg = settings.project["segments"]
    ab = seg["age_bins"]
    ages = pd.DataFrame({
        "band": [f"{ab[i]}-{ab[i + 1] - 1}" if i < len(ab) - 2 else f"{ab[i]}+" for i in range(len(ab) - 1)],
        "lower": ab[:-1], "upper": ab[1:], "sort_key": range(1, len(ab)),
    })
    write_frame(conn, ages, "analytics.cfg_age_band", primary_key=["band"])
    ib = seg["income_bins"]
    inc = pd.DataFrame({"band": seg["income_labels"], "lower": [float(v) for v in ib[:-1]],
                        "upper": [float(v) for v in ib[1:]],
                        "sort_key": range(1, len(ib))})
    write_frame(conn, inc, "analytics.cfg_income_band", primary_key=["band"])
    params = pd.DataFrame([{
        "exposure_proxy_column": risk["exposure_proxy_column"], "base_lgd": float(risk["base_lgd"]),
        "pd_clip_low": float(risk["pd_clip"][0]), "pd_clip_high": float(risk["pd_clip"][1]),
        "severe_dpd_days": int(settings.project["delinquency"]["severe_dpd_days"]),
        "feature_version": settings.feature_version,
        "is_synthetic": (settings.data_dir / "SYNTHETIC_DATA_README.txt").exists(),
    }])
    write_frame(conn, params, "analytics.cfg_risk_parameters")


# ----------------------------------------------------------------------------- feature policy
def is_excluded(column: str, settings: Settings) -> bool:
    feats = settings.features
    if column in set(feats["exclude"]):
        return True
    return any(tok in column for tok in feats.get("exclude_patterns", []))


def select_features(df: pd.DataFrame, settings: Settings) -> tuple[list[str], list[str]]:
    """Return (numeric, categorical) model feature columns allowed by configs/features.yaml."""
    max_card = settings.features["categorical_max_cardinality"]
    numeric, categorical = [], []
    for col in df.columns:
        if is_excluded(col, settings):
            continue
        if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_bool_dtype(df[col]):
            if df[col].notna().any():
                numeric.append(col)
        elif df[col].nunique(dropna=True) <= max_card:
            categorical.append(col)
        else:
            log.warning("dropping high-cardinality text column %s", col)
    return numeric, categorical


def assert_no_leakage(features: list[str], settings: Settings, X: pd.DataFrame | None = None,
                      y: pd.Series | None = None, max_single_auc: float = 0.95) -> dict:
    """Hard guard against label leakage.

    1. no excluded/label-like column name in the feature list
    2. optionally: no single numeric feature separates the label almost perfectly (AUC > max_single_auc),
       which would indicate the label was encoded into a feature.
    """
    banned = [f for f in features if is_excluded(f, settings) or re.search(r"(^|_)target($|_)", f)]
    if banned:
        raise LeakageError(f"excluded / label-like columns in feature list: {banned}")
    report = {"n_features": len(features), "max_single_feature_auc": None, "worst_feature": None}
    if X is not None and y is not None:
        from sklearn.metrics import roc_auc_score

        best, worst = 0.5, None
        for col in X.columns:
            if not pd.api.types.is_numeric_dtype(X[col]):
                continue
            s = X[col]
            mask = s.notna()
            if mask.sum() < 100 or s[mask].nunique() < 2 or y[mask].nunique() < 2:
                continue
            auc = roc_auc_score(y[mask], s[mask])
            auc = max(auc, 1 - auc)
            if auc > best:
                best, worst = auc, col
        report.update(max_single_feature_auc=round(float(best), 4), worst_feature=worst)
        if best > max_single_auc:
            raise LeakageError(f"feature {worst} alone reaches AUC {best:.3f} > {max_single_auc}: probable leakage")
    return report


# ----------------------------------------------------------------------------- registry
def _source_of(col: str) -> str:
    for prefix, src in SOURCE_PREFIXES.items():
        if col.startswith(prefix):
            return src
    if col in DERIVED_APP_COLUMNS:
        return "derived from application_{train|test}.csv"
    return "application_{train|test}.csv"


def _leakage_risk(col: str, settings: Settings) -> str:
    if col == "target":
        return "label"
    if is_excluded(col, settings):
        return "excluded"
    if col in set(settings.features.get("sensitivity_review", [])):
        return "medium (sensitivity review)"
    if col.startswith(("bur_", "prev_", "inst_", "pos_", "cc_")):
        return "low (history filtered to <= application date)"
    return "low (known at application)"


def build_feature_registry(conn: psycopg.Connection, settings: Settings) -> pd.DataFrame:
    cols = conn.execute(
        """SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema='analytics' AND table_name='customer_feature_mart' ORDER BY ordinal_position"""
    ).fetchall()
    names = [c for c, _ in cols]
    counts = conn.execute(
        "SELECT count(*), " + ", ".join(f'count("{c}")' for c in names) + " FROM analytics.customer_feature_mart "
        "WHERE source = 'train'"
    ).fetchone()
    total = max(counts[0], 1)
    desc = {}
    try:
        for _table, row, text in conn.execute(
                "SELECT \"table\", \"row\", description FROM raw.columns_description").fetchall():
            desc.setdefault(row.lower(), text)
    except psycopg.Error:
        conn.rollback()
    rows = []
    for (name, dtype), non_null in zip(cols, counts[1:], strict=True):
        rows.append({
            "feature_name": name,
            "source": _source_of(name),
            "data_type": dtype,
            "grain": "SK_ID_CURR",
            "null_ratio_train": round(1 - non_null / total, 4),
            "leakage_risk": _leakage_risk(name, settings),
            "used_in_model": not is_excluded(name, settings),
            "raw_description": desc.get(name.lower()),
        })
    reg = pd.DataFrame(rows)
    write_frame(conn, reg, "analytics.feature_registry", primary_key=["feature_name"])
    return reg


# ----------------------------------------------------------------------------- build
def mart_checks(q: QualityRunner) -> None:
    mart = "analytics.customer_feature_mart"
    q.key_not_null(mart, ["sk_id_curr"])
    q.key_unique(mart, ["sk_id_curr"])
    q.equal_counts("population_preserved", f"SELECT count(*) FROM {mart}",
                   "SELECT count(*) FROM staging.application", mart,
                   "mart customers = application population (no amplification, no loss)")
    q.equal_counts("population_preserved", f"SELECT count(*) FROM {mart} WHERE source='train'",
                   "SELECT count(*) FROM raw.application_train", mart, "train rows = application_train rows")
    q.accepted_values(mart, "target", [0, 1], where="source = 'train'")
    q.expression_count("label_absent_in_test", mart, "target", "source = 'test' AND target IS NOT NULL",
                       "application_test rows carry no TARGET")
    amp = q.scalar(f"SELECT count(*)::float / NULLIF(count(DISTINCT sk_id_curr), 0) FROM {mart}")
    q.add("join_amplification", mart, None, "pass" if amp == 1.0 else "fail", amp, "rows / distinct customers = 1.0")


def build_feature_mart(settings: Settings) -> None:
    with connect(settings) as conn, audited_run(conn, "analytics", str(settings.data_dir)) as run_id:
        write_config_tables(conn, settings)
        with timed("feature mart SQL"):
            run_sql_dir(conn, settings.sql_dir / "03_analytics", sql_params(settings))
            conn.commit()
        q = QualityRunner(conn, run_id, "analytics")
        mart_checks(q)
        q.persist()
        log.info("analytics quality summary: %s", q.summary())
        q.raise_on_fail()
        reg = build_feature_registry(conn, settings)
        log.info("feature registry: %d columns, %d model-eligible", len(reg), int(reg["used_in_model"].sum()))


def downcast(df: pd.DataFrame) -> pd.DataFrame:
    """float64 -> float32 for wide model matrices (halves memory on the full Home Credit data)."""
    floats = df.select_dtypes(include=[np.float64]).columns
    df[floats] = df[floats].astype(np.float32)
    return df
