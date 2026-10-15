"""Phase 3 — EDA & statistics (spec §11).

`run_eda(settings)` reads PostgreSQL, computes every number, draws figures and writes:
- reports/eda_report.md            (Korean prose generated from the computed numbers)
- reports/statistical_analysis.md  (test tables with effect sizes and BH-adjusted p-values)
- reports/eda_results.json         (raw numbers + `insights` for the Power BI pages)
- reports/figures/eda_*.png

Scalability: the large raw tables (bureau_balance, installments_payments, ...) are never loaded
into pandas — inventory, null ratios and missingness-vs-TARGET are aggregated in SQL. pandas only
receives the customer-level feature mart (one row per SK_ID_CURR, ~80 selected columns) and a
deterministic sample of numeric mart columns for the correlation check.

Terminology (spec §3.2/§3.4): mean(TARGET) is the "관측 상환곤란율 (Observed Difficulty Rate)",
AMT_CREDIT is the "Exposure Proxy (AMT_CREDIT)". All relationships are associations, not causes.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from psycopg import sql

from . import stats as st
from .config import PROJECT_ROOT, Settings
from .db import connect, read_frame, scalar
from .utils import timed, write_json

log = logging.getLogger(__name__)

MART = "analytics.customer_feature_mart"
TRAIN_FILTER = "source = 'train'"

# --------------------------------------------------------------------------------------------
# Feature lists (intersected with the actual mart columns at run time)
# --------------------------------------------------------------------------------------------
APP_NUMERIC = [
    "amt_income_total", "amt_credit", "amt_annuity", "amt_goods_price",
    "credit_income_ratio", "annuity_income_ratio", "credit_annuity_ratio", "goods_credit_ratio",
    "income_per_person", "age_years", "employment_years",
    "ext_source_1", "ext_source_2", "ext_source_3", "ext_source_mean",
    "cnt_children", "cnt_fam_members", "region_rating_client", "region_population_relative",
]
AGG_NUMERIC = [
    "bur_count", "bur_active_count", "bur_active_debt_credit_ratio", "bur_overdue_now_count",
    "bur_day_overdue_max", "bur_bb_dpd_month_ratio",
    "prev_count", "prev_refused_ratio", "prev_approved_ratio",
    "inst_late_ratio", "inst_severe_late_ratio", "inst_dpd_max", "inst_payment_ratio_mean",
    "inst_underpaid_ratio", "pos_dpd_max", "pos_dpd_month_ratio", "cc_utilization_mean", "cc_dpd_max",
]
CATEGORICAL = [
    "name_contract_type", "code_gender", "flag_own_car", "flag_own_realty", "name_type_suite",
    "name_income_type", "name_education_type", "name_family_status", "name_housing_type",
    "occupation_type", "organization_type", "weekday_appr_process_start", "prev_last_status",
]
HISTORY_FLAGS = ["has_bureau", "has_previous", "has_installments", "has_pos_cash", "has_credit_card"]
EXTRA = ["target", "days_employed_anom", "bur_bb_worst_status", "ext_source_missing_count"]
AMOUNT_COLUMNS = ["amt_income_total", "amt_credit", "amt_annuity", "amt_goods_price"]
AGG_PREFIXES = ("bur_", "prev_", "inst_", "pos_", "cc_")

# Fairness-sensitive attributes: described only, never used for insights/approval criteria (spec §19).
SENSITIVE = {
    "code_gender": "성별", "name_family_status": "가족 상태", "cnt_children": "자녀 수",
    "cnt_fam_members": "가구원 수", "age_years": "연령", "days_birth": "연령(일)", "age_band": "연령대",
}

# Palette: navy/neutral for base, amber/red only for elevated risk.
NAVY, NAVY_LIGHT, GREY, GREY_LIGHT = "#1F3A5F", "#5B7DA8", "#8A94A6", "#D5DBE3"
AMBER, RED = "#E0A030", "#B8423A"

RARE_SHARE = 0.01          # categorical level share below this is "rare"
CORR_THRESHOLD = 0.9       # |r| above this is a redundant pair
CORR_SAMPLE = 50_000       # deterministic sample size for the correlation matrix
ELEVATED_RATIO = 1.2       # segment rate >= overall * this is "elevated"
MIN_MISSING_SHARE = 0.01   # missingness insights/tables require at least this NULL share
# max statistical insights per kind (first pass); remaining slots are filled by effect size
INSIGHT_QUOTA = {"numeric_effect": 4, "binned_gradient": 2, "categorical_association": 1, "missingness": 1}

SYNTHETIC_BANNER = "⚠️ 합성(synthetic) 테스트 데이터 기반 결과 — 실제 Home Credit 분석 결과 아님"
RATE_NAME = "관측 상환곤란율 (Observed Difficulty Rate)"
EXPOSURE_NAME = "Exposure Proxy (AMT_CREDIT)"


# ============================================================================================
# Helpers
# ============================================================================================
def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def pct(x: Any, digits: int = 1) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{100 * float(x):.{digits}f}%"


def num(x: Any, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    x = float(x)
    if abs(x) >= 1e5:
        return f"{x:,.0f}"
    return f"{x:,.{digits}f}"


def pval(p: Any) -> str:
    if p is None or not np.isfinite(p):
        return "n/a"
    return "<1e-300" if p == 0 else (f"{p:.2e}" if p < 1e-3 else f"{p:.3f}")


def md_table(df: pd.DataFrame, columns: dict[str, tuple[str, Any]], max_rows: int | None = None) -> str:
    """Render a markdown table. `columns` maps column -> (header, formatter)."""
    if df is None or df.empty:
        return "_(해당 없음)_\n"
    rows = df.head(max_rows) if max_rows else df
    header = "| " + " | ".join(h for h, _ in columns.values()) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, sep]
    for _, r in rows.iterrows():
        cells = []
        for col, (_, fmt) in columns.items():
            v = r.get(col)
            cells.append(fmt(v) if fmt else ("" if v is None else str(v)))
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def to_jsonable(obj: Any) -> Any:
    """Recursively convert numpy/pandas objects; non-finite floats become None (valid JSON)."""
    if isinstance(obj, pd.DataFrame):
        return [to_jsonable(r) for r in obj.to_dict(orient="records")]
    if isinstance(obj, pd.Series):
        return to_jsonable(obj.to_dict())
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, Path):
        return _rel(obj)
    return obj


def feature_family(name: str) -> str:
    """Group closely related features so insights do not repeat the same signal."""
    if name.startswith("ext_source"):
        return "ext_source"
    if name.startswith("inst_") and "late" in name:
        return "inst_late"
    return re.sub(r"_(mean|max|min|sum|total|ratio|count|band|bin)$", "", name)


def is_sensitive(name: str) -> bool:
    return name in SENSITIVE


def powerbi_page(feature: str) -> str:
    if feature.startswith(AGG_PREFIXES) or feature.startswith("has_"):
        return "Page 3. Repayment & Delinquency"
    return "Page 2. Risk Segmentation"


# ============================================================================================
# Database-side computations (no large raw table is materialised in pandas)
# ============================================================================================
def detect_synthetic(conn: psycopg.Connection) -> bool:
    """True when raw.columns_description descriptions are tagged '[synthetic]' (test data)."""
    try:
        share = scalar(
            conn,
            "SELECT avg(CASE WHEN description LIKE '[synthetic]%%' THEN 1.0 ELSE 0.0 END) "
            "FROM raw.columns_description",
        )
    except psycopg.Error:
        conn.rollback()
        return False
    return share is not None and float(share) > 0.5


def table_columns(conn: psycopg.Connection, schema: str, table: str) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table),
    ).fetchall()
    return pd.DataFrame(rows, columns=["column_name", "data_type"])


def _audit_duplicates(conn: psycopg.Connection) -> dict[str, float]:
    """Latest raw key_unique duplicate counts from audit (avoids re-scanning huge tables)."""
    try:
        rows = conn.execute(
            """SELECT DISTINCT ON (table_name) table_name, observed_value
                 FROM audit.data_quality_result
                WHERE check_name = 'key_unique'
                ORDER BY table_name, checked_at DESC, result_id DESC"""
        ).fetchall()
    except psycopg.Error:
        conn.rollback()
        return {}
    return {t: v for t, v in rows}


def table_inventory(conn: psycopg.Connection, settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rows, columns, storage size, key duplicates and per-column null ratios — all in SQL.

    Returns (inventory, column_nulls). One sequential scan per table (count(col) per column).
    Key duplicates come from the latest audit `key_unique` check when present, else from SQL.
    """
    null_warn = float(settings.project["quality"]["null_ratio_warn"])
    tables = [(f"raw.{name}", cfg.get("unique_key")) for name, cfg in settings.project["sources"].items()]
    tables.append((MART, ["SK_ID_CURR"]))
    audit_dups = _audit_duplicates(conn)
    inv_rows, null_rows = [], []
    for full, keys in tables:
        schema, name = full.split(".")
        cols = table_columns(conn, schema, name)
        if cols.empty:
            log.warning("inventory: %s not found, skipped", full)
            continue
        col_names = cols["column_name"].tolist()
        counts = sql.SQL(", ").join(
            [sql.SQL("count(*)")] + [sql.SQL("count({})").format(sql.Identifier(c)) for c in col_names]
        )
        row = conn.execute(sql.SQL("SELECT {} FROM {}").format(counts, sql.Identifier(schema, name))).fetchone()
        n_rows = int(row[0])
        null_ratio = {c: (1 - row[i + 1] / n_rows) if n_rows else np.nan for i, c in enumerate(col_names)}
        size_mb = scalar(conn, "SELECT pg_total_relation_size(%s::regclass) / 1048576.0", (full,))
        dup = None
        key_str = None
        if keys:
            key_cols = [k.lower() for k in keys]
            key_str = ",".join(key_cols)
            if full in audit_dups and full != MART:
                dup = audit_dups[full]
            else:
                ident = sql.SQL(", ").join(map(sql.Identifier, key_cols))
                expr = sql.SQL("({})").format(ident) if len(key_cols) > 1 else ident
                dup = scalar(conn, sql.SQL("SELECT count(*) - count(DISTINCT {}) FROM {}").format(
                    expr, sql.Identifier(schema, name)))
        top_null = sorted(null_ratio.items(), key=lambda kv: -kv[1])[:5]
        inv_rows.append({
            "table": full,
            "rows": n_rows,
            "columns": len(col_names),
            "size_mb": float(size_mb or 0),
            "unique_key": key_str,
            "key_duplicates": None if dup is None else int(dup),
            "mean_null_ratio": float(np.mean(list(null_ratio.values()))) if null_ratio else np.nan,
            "cols_null_gt_warn": int(sum(v > null_warn for v in null_ratio.values())),
            "cols_all_null": int(sum(v >= 1.0 for v in null_ratio.values())),
            "top_null_columns": ", ".join(f"{c} ({pct(v)})" for c, v in top_null if v > 0) or "-",
        })
        null_rows += [{"table": full, "column": c, "null_ratio": v} for c, v in null_ratio.items()]
    return pd.DataFrame(inv_rows), pd.DataFrame(null_rows)


def dq_status_summary(conn: psycopg.Connection) -> pd.DataFrame:
    """Status counts of the latest result per (layer, check, table, column) in audit."""
    try:
        return read_frame(conn, """
            SELECT layer, status, count(*) AS checks FROM (
              SELECT DISTINCT ON (layer, check_name, table_name, column_name) layer, status
                FROM audit.data_quality_result
               ORDER BY layer, check_name, table_name, column_name, checked_at DESC, result_id DESC) t
            GROUP BY 1, 2 ORDER BY 1, 2""")
    except psycopg.Error:
        conn.rollback()
        return pd.DataFrame(columns=["layer", "status", "checks"])


def missingness_vs_target(conn: psycopg.Connection, columns: list[str]) -> pd.DataFrame:
    """Observed difficulty rate when a mart column is NULL vs non-NULL (train), computed in SQL.

    NULL in bur_/prev_/inst_/pos_/cc_ aggregates means "no history in that source" (structural).
    """
    parts = [sql.SQL("count(*)"), sql.SQL("sum(target)")]
    for c in columns:
        ident = sql.Identifier(c)
        parts.append(sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(ident))
        parts.append(sql.SQL("coalesce(sum(target) FILTER (WHERE {} IS NULL), 0)").format(ident))
    q = sql.SQL("SELECT {} FROM {} WHERE " + TRAIN_FILTER).format(
        sql.SQL(", ").join(parts), sql.Identifier(*MART.split(".")))
    row = conn.execute(q).fetchone()
    n, events = int(row[0]), int(row[1] or 0)
    out = []
    for i, c in enumerate(columns):
        n_null, e_null = int(row[2 + 2 * i]), int(row[3 + 2 * i])
        n_nn, e_nn = n - n_null, events - e_null
        rec = {
            "column": c, "n": n, "n_missing": n_null, "missing_ratio": n_null / n if n else np.nan,
            "rate_missing": e_null / n_null if n_null else np.nan,
            "rate_present": e_nn / n_nn if n_nn else np.nan,
            "structural": c.startswith(AGG_PREFIXES),
        }
        if 0 < n_null < n:
            res = st.chi_square([[e_null, n_null - e_null], [e_nn, n_nn - e_nn]])
            rec.update({"test": res["test"], "statistic": res["statistic"], "p_value": res["p_value"],
                        "effect_size": res["effect_size"], "effect_label": res["effect_label"]})
        out.append(rec)
    df = pd.DataFrame(out)
    for col in ("test", "statistic", "p_value", "effect_size", "effect_label"):
        if col not in df:
            df[col] = np.nan
    df["rate_diff"] = df["rate_missing"] - df["rate_present"]
    return df


def load_train_frame(conn: psycopg.Connection, columns: list[str]) -> pd.DataFrame:
    cols = sql.SQL(", ").join(map(sql.Identifier, columns)).as_string(conn)
    return read_frame(conn, f"SELECT {cols} FROM {MART} WHERE {TRAIN_FILTER}")


def load_numeric_sample(conn: psycopg.Connection, columns: list[str], n: int) -> pd.DataFrame:
    """Deterministic (md5-ordered) sample of numeric mart columns for the correlation check."""
    cols = sql.SQL(", ").join(map(sql.Identifier, columns)).as_string(conn)
    return read_frame(
        conn, f"SELECT {cols} FROM {MART} WHERE {TRAIN_FILTER} ORDER BY md5(sk_id_curr::text) LIMIT {int(n)}")


# ============================================================================================
# pandas-side analyses (customer-level frame only)
# ============================================================================================
def target_balance(df: pd.DataFrame) -> dict[str, Any]:
    n = int(df["target"].notna().sum())
    events = int(df["target"].sum())
    low, high = st.wilson_ci(events, n)
    return {"n": n, "events": events, "non_events": n - events, "rate": events / n if n else np.nan,
            "ci_low": low, "ci_high": high, "imbalance_ratio": (n - events) / events if events else np.nan}


def numeric_profile(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Distribution summary with far-outlier counts (outside Q1-3*IQR, Q3+3*IQR)."""
    rows = []
    for c in columns:
        s = pd.to_numeric(df[c], errors="coerce")
        v = s.dropna()
        if v.empty:
            continue
        q = v.quantile([0.01, 0.25, 0.5, 0.75, 0.99])
        iqr = q[0.75] - q[0.25]
        lo, hi = q[0.25] - 3 * iqr, q[0.75] + 3 * iqr
        # IQR = 0 (discrete / concentrated feature): the fence rule is meaningless -> n/a
        far = ((v < lo) | (v > hi)) if iqr > 0 else None
        rows.append({
            "feature": c, "n": int(v.size), "missing_ratio": float(s.isna().mean()),
            "mean": float(v.mean()), "std": float(v.std()), "min": float(v.min()), "p01": float(q[0.01]),
            "median": float(q[0.5]), "p99": float(q[0.99]), "max": float(v.max()),
            "skew": float(v.skew()), "far_outliers": int(far.sum()) if far is not None else None,
            "far_outlier_ratio": float(far.mean()) if far is not None else np.nan,
            "sensitive": is_sensitive(c),
        })
    return pd.DataFrame(rows)


def categorical_profile(df: pd.DataFrame, columns: list[str], rare_share: float = RARE_SHARE) -> pd.DataFrame:
    rows = []
    for c in columns:
        s = df[c]
        vc = s.value_counts(dropna=True)
        share = vc / len(s)
        rare = share[share < rare_share]
        rows.append({
            "feature": c, "levels": int(vc.size), "missing_ratio": float(s.isna().mean()),
            "top_level": str(vc.index[0]) if len(vc) else None,
            "top_share": float(share.iloc[0]) if len(vc) else np.nan,
            "rare_levels": int(rare.size), "rare_share_total": float(rare.sum()),
            "rare_examples": ", ".join(map(str, rare.index[:5])),
            "sensitive": is_sensitive(c),
        })
    return pd.DataFrame(rows)


def target_comparison(df: pd.DataFrame, columns: list[str], family: str) -> pd.DataFrame:
    """Mann–Whitney U (+ rank-biserial) for each feature; Welch t (+ Cohen's d) where appropriate.

    Welch is reported only when the pooled |skew| < 2 (mean is a meaningful summary); heavy-tailed
    amounts rely on Mann–Whitney. Aggregates are compared among customers with history (non-NULL).
    """
    y = df["target"]
    rows = []
    for c in columns:
        x = pd.to_numeric(df[c], errors="coerce")
        g1, g0 = x[y == 1].dropna().to_numpy(), x[y == 0].dropna().to_numpy()
        mw = st.mann_whitney(g1, g0)
        skew = float(x.skew()) if x.notna().sum() > 2 else np.nan
        rec = {"family": family, "feature": c, "sensitive": is_sensitive(c), "skew": skew,
               "coverage": float(x.notna().mean()), **mw}
        if np.isfinite(skew) and abs(skew) < 2:
            w = st.welch_t(g1, g0)
            rec.update({"welch_t": w["statistic"], "welch_p": w["p_value"], "cohens_d": w["effect_size"],
                        "mean1": w.get("mean1"), "mean0": w.get("mean0")})
        else:
            rec.update({"welch_t": np.nan, "welch_p": np.nan, "cohens_d": np.nan})
        rows.append(rec)
    return pd.DataFrame(rows)


def categorical_tests(df: pd.DataFrame, columns: list[str], family: str) -> pd.DataFrame:
    rows = []
    for c in columns:
        res = st.crosstab_test(df[c], df["target"])
        rows.append({"family": family, "feature": c, "sensitive": is_sensitive(c), "n1": int(df["target"].sum()),
                     "n0": int((df["target"] == 0).sum()), **res})
    return pd.DataFrame(rows)


# ---- binning ---------------------------------------------------------------------------------
def _ordered(values: pd.Series, order: list[str]) -> pd.Series:
    present = [o for o in order if o in set(values.dropna())]
    return pd.Series(pd.Categorical(values, categories=present, ordered=True), index=values.index)


def age_band(age: pd.Series, bins: list[float]) -> pd.Series:
    labels = [f"{int(a)}-{int(b)}" for a, b in zip(bins[:-1], bins[1:], strict=True)]
    return pd.cut(age, bins=bins, labels=labels, right=False)


def income_band(income: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(income, bins=bins, labels=labels, right=False)


def employment_band(years: pd.Series, anomaly: pd.Series) -> pd.Series:
    edges = [0, 1, 3, 5, 10, 20, np.inf]
    labels = ["<1y", "1-3y", "3-5y", "5-10y", "10-20y", "20y+"]
    band = pd.cut(years, bins=edges, labels=labels, right=False).astype(object)
    band = band.where(anomaly.fillna(0) != 1, "Sentinel 365243")
    return _ordered(band, labels + ["Sentinel 365243"])


def quantile_band(x: pd.Series, q: int, prefix: str) -> pd.Series:
    """Quantile bins labelled {prefix}1 (lowest values) .. {prefix}q; NaN stays NaN."""
    codes = pd.qcut(x, q=q, labels=False, duplicates="drop")
    labels = [f"{prefix}{i + 1}" for i in range(int(np.nanmax(codes)) + 1)] if codes.notna().any() else []
    band = codes.map(lambda v: f"{prefix}{int(v) + 1}" if pd.notna(v) else np.nan)
    return _ordered(band, labels)


def ratio_band(x: pd.Series, edges: list[float], no_history: str = "No history") -> pd.Series:
    """0 / (e0,e1] / ... / >e_last bands; NULL -> 'No history' (aggregate absent in that source)."""
    labels = ["0"] + [f"{100 * a:g}-{100 * b:g}%" for a, b in zip([0, *edges[:-1]], edges, strict=True)]
    labels.append(f">{100 * edges[-1]:g}%")
    out = pd.Series(np.nan, index=x.index, dtype=object)
    out[x.isna()] = no_history
    out[x == 0] = "0"
    lower = 0.0
    for edge, label in zip(edges, labels[1:-1], strict=True):
        out[(x > lower) & (x <= edge)] = label
        lower = edge
    out[x > edges[-1]] = labels[-1]
    return _ordered(out, [no_history, *labels])


def status_band(x: pd.Series, no_history: str = "No history") -> pd.Series:
    out = x.map(lambda v: no_history if pd.isna(v) else f"Status {int(v)}")
    order = [no_history] + [f"Status {i}" for i in range(0, 10)]
    return _ordered(out, order)


def binned_risk(df: pd.DataFrame, band: pd.Series, name: str, source: str) -> dict[str, Any]:
    """Rate table (Wilson CI, exposure share) + chi-square/Cramér's V of the band vs TARGET."""
    table = st.rate_table(band, df["target"], df["amt_credit"])
    test = st.crosstab_test(band, df["target"])
    return {"name": name, "source": source, "table": table, "test": test, "sensitive": is_sensitive(name)}


# ---- exposure concentration -------------------------------------------------------------------
def exposure_concentration(table: pd.DataFrame, overall_rate: float, min_n: int,
                           ratio: float = ELEVATED_RATIO) -> dict[str, Any]:
    """Customer share vs exposure share for segments whose observed difficulty rate is elevated."""
    t = table[table["n"] >= min_n]
    elevated = t[t["rate"] >= overall_rate * ratio]
    total_events = table["events"].sum()
    return {
        "elevated_groups": elevated["group"].astype(str).tolist(),
        "customer_share": float(elevated["customer_share"].sum()),
        "exposure_share": float(elevated["exposure_share"].sum()),
        "event_share": float(elevated["events"].sum() / total_events) if total_events else np.nan,
        "rate": float(elevated["events"].sum() / elevated["n"].sum()) if len(elevated) else np.nan,
        "max_exposure_to_customer_ratio": float(t["exposure_to_customer_ratio"].max()) if len(t) else np.nan,
    }


def portfolio_concentration(exposure: pd.Series) -> dict[str, float]:
    """Top-decile exposure share and Gini coefficient of the exposure proxy."""
    v = np.sort(exposure.dropna().to_numpy(dtype=float))
    n = v.size
    if n == 0 or v.sum() == 0:
        return {"top10_share": np.nan, "top1_share": np.nan, "gini": np.nan}
    cum = np.cumsum(v)
    gini = (n + 1 - 2 * (cum / cum[-1]).sum()) / n
    return {
        "top10_share": float(v[-max(1, n // 10):].sum() / v.sum()),
        "top1_share": float(v[-max(1, n // 100):].sum() / v.sum()),
        "gini": float(gini),
        "total_exposure": float(v.sum()),
        "median_exposure": float(np.median(v)),
    }


def score_tail_concentration(df: pd.DataFrame, score: str = "ext_source_mean", tail: float = 0.2) -> dict:
    """Share of customers / exposure / events in the lowest-score tail (score is a risk proxy)."""
    s = df[score]
    valid = df[s.notna()]
    if valid.empty:
        return {}
    cut = valid[score].quantile(tail)
    low = valid[valid[score] <= cut]
    return {
        "score": score, "tail": tail, "cutoff": float(cut), "n_valid": int(len(valid)),
        "customer_share": len(low) / len(valid),
        "exposure_share": float(low["amt_credit"].sum() / valid["amt_credit"].sum()),
        "event_share": float(low["target"].sum() / valid["target"].sum()) if valid["target"].sum() else np.nan,
        "rate_tail": float(low["target"].mean()), "rate_rest": float(valid.loc[valid[score] > cut, "target"].mean()),
    }


# ---- correlation -------------------------------------------------------------------------------
def correlation_check(sample: pd.DataFrame, threshold: float = CORR_THRESHOLD) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pearson correlation on the sample; returns (corr matrix, |r| > threshold pairs)."""
    num_df = sample.apply(pd.to_numeric, errors="coerce")
    num_df = num_df.loc[:, num_df.nunique(dropna=True) > 1]
    corr = num_df.corr(method="pearson", min_periods=max(30, len(num_df) // 100))
    upper = corr.where(np.triu(np.ones(corr.shape, dtype=bool), k=1))
    pairs = upper.stack().rename("r").reset_index()
    pairs.columns = ["feature_a", "feature_b", "r"]
    pairs = pairs[pairs["r"].abs() > threshold].copy()
    pairs["abs_r"] = pairs["r"].abs()
    return corr, pairs.sort_values("abs_r", ascending=False).reset_index(drop=True)


def vif_table(sample: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Variance inflation factors (diag of the inverse correlation matrix) on complete rows."""
    cols = [c for c in columns if c in sample.columns]
    data = sample[cols].apply(pd.to_numeric, errors="coerce").dropna()
    data = data.loc[:, data.std() > 0]
    if len(data) < len(data.columns) + 10:
        return pd.DataFrame(columns=["feature", "vif"])
    inv = np.linalg.pinv(np.corrcoef(data.to_numpy(), rowvar=False))
    return pd.DataFrame({"feature": data.columns, "vif": np.diag(inv), "n_complete": len(data)}) \
        .sort_values("vif", ascending=False).reset_index(drop=True)


# ============================================================================================
# Figures
# ============================================================================================
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 130, "font.size": 9.5, "axes.titlesize": 11,
        "axes.titleweight": "bold", "axes.edgecolor": GREY, "axes.labelcolor": "#2B2B2B",
        "axes.spines.top": False, "axes.spines.right": False, "xtick.color": "#444444",
        "ytick.color": "#444444", "axes.grid": True, "axes.axisbelow": True, "grid.color": "#ECEFF3", "grid.linewidth": 0.8,
    })
    return plt


def _pct_fmt():
    from matplotlib.ticker import PercentFormatter

    return PercentFormatter(xmax=1.0, decimals=None)


def _save(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return _rel(path)


def _risk_color(rate: float, overall: float) -> str:
    if not np.isfinite(rate):
        return GREY
    if rate >= overall * 1.5:
        return RED
    if rate >= overall * ELEVATED_RATIO:
        return AMBER
    return NAVY


def plot_target_balance(bal: dict, path: Path) -> str:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(5.5, 3.6))
    vals = [bal["non_events"], bal["events"]]
    bars = ax.bar(["TARGET = 0", "TARGET = 1"], vals, color=[NAVY, RED], width=0.55)
    for b, v in zip(bars, vals, strict=True):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,}\n({v / bal['n']:.1%})", ha="center", va="bottom")
    ax.set_ylabel("Customers (train)")
    ax.set_ylim(0, max(vals) * 1.22)
    ax.set_title(f"TARGET balance — Observed Difficulty Rate {bal['rate']:.2%}")
    return _save(fig, path)


def plot_numeric_distributions(df: pd.DataFrame, path: Path) -> str:
    plt = _plt()
    specs = [(c, c in AMOUNT_COLUMNS) for c in
             ["amt_income_total", "amt_credit", "amt_annuity", "credit_income_ratio", "annuity_income_ratio",
              "ext_source_mean"] if c in df.columns]
    fig, axes = plt.subplots(2, 3, figsize=(11, 6))
    for ax, (c, log) in zip(axes.ravel(), specs, strict=False):
        v = pd.to_numeric(df[c], errors="coerce").dropna()
        v = v[v > 0] if log else v
        if log:
            v = np.log10(v)
        else:
            v = v.clip(upper=v.quantile(0.995))
        ax.hist(v, bins=50, color=NAVY_LIGHT, edgecolor="white", linewidth=0.3)
        ax.axvline(v.median(), color=AMBER, linestyle="--", linewidth=1.2, label="median")
        ax.set_title(f"log10({c})" if log else f"{c} (clipped p99.5)")
        ax.legend(frameon=False, fontsize=8)
    for ax in axes.ravel()[len(specs):]:
        ax.set_visible(False)
    fig.suptitle("Numeric distributions (train)", fontweight="bold")
    return _save(fig, path)


def plot_categorical_frequency(df: pd.DataFrame, columns: list[str], path: Path) -> str:
    plt = _plt()
    cols = [c for c in columns if c in df.columns][:3]
    fig, axes = plt.subplots(1, len(cols), figsize=(5 * len(cols), 5.2))
    axes = np.atleast_1d(axes)
    for ax, c in zip(axes, cols, strict=True):
        share = df[c].fillna("(missing)").value_counts(normalize=True).head(15)[::-1]
        colors = [AMBER if s < RARE_SHARE else NAVY for s in share]
        ax.barh(share.index.astype(str), share.values, color=colors)
        ax.set_title(c)
        ax.set_xlabel("Customer share")
        ax.xaxis.set_major_formatter(_pct_fmt())
    fig.suptitle(f"Categorical frequencies (amber = rare, share < {RARE_SHARE:.0%})", fontweight="bold")
    return _save(fig, path)


def plot_target_boxplots(df: pd.DataFrame, columns: list[str], path: Path) -> str:
    plt = _plt()
    cols = [c for c in columns if c in df.columns][:6]
    fig, axes = plt.subplots(2, 3, figsize=(11, 6.5))
    for ax, c in zip(axes.ravel(), cols, strict=False):
        data = [pd.to_numeric(df.loc[df["target"] == t, c], errors="coerce").dropna() for t in (0, 1)]
        bp = ax.boxplot(data, tick_labels=["TARGET=0", "TARGET=1"], showfliers=False, patch_artist=True,
                        widths=0.5, medianprops={"color": "white", "linewidth": 1.5})
        for patch, color in zip(bp["boxes"], [NAVY, RED], strict=True):
            patch.set_facecolor(color)
        if c in AMOUNT_COLUMNS:
            ax.set_yscale("log")
        ax.set_title(c)
    for ax in axes.ravel()[len(cols):]:
        ax.set_visible(False)
    fig.suptitle("Amounts and ratios by TARGET (outliers hidden)", fontweight="bold")
    return _save(fig, path)


def plot_rate_bars(table: pd.DataFrame, overall: float, title: str, xlabel: str, path: Path) -> str:
    plt = _plt()
    t = table.reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(max(6, 0.65 * len(t) + 2.5), 4))
    x = np.arange(len(t))
    colors = [_risk_color(r, overall) for r in t["rate"]]
    yerr = np.vstack([(t["rate"] - t["ci_low"]).clip(lower=0), (t["ci_high"] - t["rate"]).clip(lower=0)])
    ax.bar(x, t["rate"], color=colors, yerr=yerr, capsize=3, ecolor="#555555", width=0.65)
    ax.axhline(overall, color=GREY, linestyle="--", linewidth=1, label=f"overall {overall:.1%}")
    for xi, (r, hi) in enumerate(zip(t["rate"], t["ci_high"], strict=True)):
        ax.text(xi, max(r, hi) + overall * 0.03, f"{r:.1%}", ha="center", va="bottom", fontsize=7.5, color="#222222")
    labels = [f"{g}\nn={int(n):,}" for g, n in zip(t["group"].astype(str), t["n"], strict=True)]
    ax.set_xticks(x, labels, rotation=30 if len(t) > 6 else 0, ha="right" if len(t) > 6 else "center", fontsize=8)
    ax.set_ylim(0, max(t["ci_high"].max(), overall) * 1.15)
    ax.set_ylabel("Observed Difficulty Rate (95% Wilson CI)")
    ax.yaxis.set_major_formatter(_pct_fmt())
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.legend(frameon=False, loc="upper right")
    return _save(fig, path)


def plot_missingness(miss: pd.DataFrame, overall: float, path: Path, top: int = 15) -> str:
    plt = _plt()
    m = miss.dropna(subset=["effect_size"])
    m = m[m["missing_ratio"] >= MIN_MISSING_SHARE].sort_values("effect_size", ascending=False).head(top)[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.38 * len(m) + 1.8))
    y = np.arange(len(m))
    ax.hlines(y, m["rate_present"], m["rate_missing"], color=GREY_LIGHT, linewidth=3)
    ax.scatter(m["rate_present"], y, color=NAVY, label="value present", zorder=3)
    ax.scatter(m["rate_missing"], y, color=AMBER, label="value missing (NULL)", zorder=3)
    ax.axvline(overall, color=GREY, linestyle="--", linewidth=1)
    ax.set_yticks(y, [f"{c} ({r:.0%} null)" for c, r in zip(m["column"], m["missing_ratio"], strict=True)])
    ax.xaxis.set_major_formatter(_pct_fmt())
    ax.set_xlabel("Observed Difficulty Rate")
    ax.set_title(f"Missingness vs TARGET (top by Cramér's V, NULL share >= {MIN_MISSING_SHARE:.0%})")
    ax.legend(frameon=False, loc="best")
    return _save(fig, path)


def plot_exposure(table: pd.DataFrame, overall: float, title: str, path: Path) -> str:
    plt = _plt()
    t = table.reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(max(6.5, 0.8 * len(t) + 3), 4.2))
    x = np.arange(len(t))
    w = 0.38
    ax.bar(x - w / 2, t["customer_share"], width=w, color=GREY_LIGHT, label="Customer share")
    ax.bar(x + w / 2, t["exposure_share"], width=w, color=NAVY, label="Exposure Proxy (AMT_CREDIT) share")
    ax.yaxis.set_major_formatter(_pct_fmt())
    ax.set_ylabel("Share of portfolio")
    ax.set_xticks(x, t["group"].astype(str), rotation=30 if len(t) > 6 else 0, ha="right" if len(t) > 6 else "center")
    ax2 = ax.twinx()
    ax2.plot(x, t["rate"], color=RED, marker="o", linewidth=1.5, label="Observed Difficulty Rate")
    ax2.axhline(overall, color=AMBER, linestyle="--", linewidth=1, label=f"overall rate {overall:.1%}")
    ax2.yaxis.set_major_formatter(_pct_fmt())
    ax2.set_ylabel("Observed Difficulty Rate", color=RED)
    ax2.set_ylim(0, max(t["rate"].max() * 1.3, overall * 1.5))
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8, loc="upper left")
    ax.set_title(title)
    return _save(fig, path)


def plot_effect_sizes(tests: pd.DataFrame, path: Path, top: int = 20) -> str:
    plt = _plt()
    t = tests.dropna(subset=["effect_size"]).copy()
    t["abs"] = t["effect_size"].abs()
    t = t.sort_values("abs", ascending=False).head(top)[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.33 * len(t) + 1.6))
    colors = [GREY if s else (AMBER if e > 0 else NAVY) for e, s in zip(t["effect_size"], t["sensitive"], strict=True)]
    ax.barh(t["feature"], t["effect_size"], color=colors)
    ax.axvline(0, color="#444444", linewidth=0.8)
    for v in (-0.1, 0.1):
        ax.axvline(v, color=GREY_LIGHT, linestyle=":", linewidth=1)
    ax.set_xlabel("rank-biserial r  (>0: higher in TARGET=1, <0: lower in TARGET=1)")
    ax.set_title("Effect sizes, TARGET=1 vs TARGET=0 (grey = fairness-sensitive, descriptive only)")
    return _save(fig, path)


def plot_correlation(corr: pd.DataFrame, columns: list[str], path: Path) -> str:
    plt = _plt()
    cols = [c for c in columns if c in corr.columns]
    c = corr.loc[cols, cols]
    fig, ax = plt.subplots(figsize=(0.42 * len(cols) + 3, 0.42 * len(cols) + 2))
    im = ax.imshow(c.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(cols)), cols, rotation=90, fontsize=7.5)
    ax.set_yticks(range(len(cols)), cols, fontsize=7.5)
    ax.grid(False)
    for i in range(len(cols)):
        for j in range(len(cols)):
            v = c.iat[i, j]
            if np.isfinite(v) and abs(v) > CORR_THRESHOLD and i != j:
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=6, color="white")
    fig.colorbar(im, ax=ax, fraction=0.04, label="Pearson r")
    ax.set_title(f"Correlation of key features (|r| > {CORR_THRESHOLD} annotated)")
    return _save(fig, path)


# ============================================================================================
# Insights
# ============================================================================================
def _source_sql(*cols: str) -> str:
    return f"SELECT target, {', '.join(cols)} FROM {MART} WHERE {TRAIN_FILTER}"


def select_insights(res: dict[str, Any], max_insights: int = 10, min_insights: int = 5) -> list[dict[str, Any]]:
    """Rank candidate findings by effect size (statistical) or exposure concentration.

    Fairness-sensitive attributes are excluded. One insight per feature family. Statistical insights
    are ordered by effect size (|rank-biserial r| or Cramér's V); exposure insights by the exposure
    share held in elevated-rate segments. Wording is neutral (association, not causation).
    """
    overall = res["target_balance"]["rate"]
    cands: list[dict[str, Any]] = []

    for _, r in res["tests_numeric"].iterrows():
        if r["sensitive"] or not np.isfinite(r["effect_size"]):
            continue
        direction = "높은" if r["effect_size"] > 0 else "낮은"
        scope = " (해당 이력 보유 고객 기준)" if r["family"] == "aggregate" else ""
        if np.isclose(r["median1"], r["median0"]):
            med = f"중앙값은 두 그룹 모두 {num(r['median1'], 3)}로 같지만 분포는 TARGET=1 쪽이 더 {direction} 값으로 치우쳐 관측된다"
        else:
            med = (f"중앙값은 {num(r['median1'], 3)}로 TARGET=0 고객군({num(r['median0'], 3)})보다 "
                   f"{'높게' if r['median1'] > r['median0'] else '낮게'} 관측된다")
        cands.append({
            "kind": "numeric_effect", "feature": r["feature"], "strength": abs(r["effect_size"]),
            "metric": "rank-biserial r", "value": r["effect_size"],
            "title": f"{r['feature']}: TARGET=1 고객군에서 더 {direction} 값 분포",
            "statement": (f"TARGET=1 고객군의 `{r['feature']}` {med}{scope}. rank-biserial r = "
                          f"{r['effect_size']:+.3f} (효과크기 {st.EFFECT_LABEL_KO.get(r['effect_label'], 'n/a')})."),
            "evidence": {"figure": res["figures"].get("effect_sizes"), "function": "credit_risk.eda.target_comparison",
                         "sql": _source_sql(r["feature"])},
        })

    for b in res["binned"]:
        t, test = b["table"], b["test"]
        if b["sensitive"] or not np.isfinite(test.get("effect_size", np.nan)):
            continue
        tt = t[t["n"] >= res["min_segment_n"]]
        if len(tt) < 2:
            continue
        hi, lo = tt.loc[tt["rate"].idxmax()], tt.loc[tt["rate"].idxmin()]
        cands.append({
            "kind": "binned_gradient", "feature": b["source"], "strength": test["effect_size"],
            "metric": "Cramér's V", "value": test["effect_size"],
            "title": f"{b['name']} 구간별 관측 상환곤란율 차이",
            "statement": (f"`{b['name']}` 구간별 {RATE_NAME}은 최저 {pct(lo['rate'])}(`{lo['group']}`, n={int(lo['n']):,})"
                          f"에서 최고 {pct(hi['rate'])}(`{hi['group']}`, n={int(hi['n']):,})까지 분포한다 "
                          f"(전체 {pct(overall)}, Cramér's V = {test['effect_size']:.3f})."),
            "evidence": {"figure": res["figures"].get(f"rate_{b['name']}"), "function": "credit_risk.eda.binned_risk",
                         "sql": _source_sql(b["source"], "amt_credit")},
        })

    for _, r in res["tests_categorical"].iterrows():
        if r["sensitive"] or not np.isfinite(r["effect_size"]):
            continue
        rt = res["categorical_rates"].get(r["feature"])
        detail = ""
        if rt is not None and len(rt):
            rr = rt[rt["n"] >= res["min_segment_n"]]
            if len(rr):
                top = rr.loc[rr["rate"].idxmax()]
                detail = f" 최고 구간은 `{top['group']}` ({pct(top['rate'])}, 고객 비중 {pct(top['customer_share'])})."
        cands.append({
            "kind": "categorical_association", "feature": r["feature"], "strength": r["effect_size"],
            "metric": "Cramér's V", "value": r["effect_size"],
            "title": f"{r['feature']} 범주별 관측 상환곤란율 차이",
            "statement": f"`{r['feature']}`와 TARGET의 연관은 Cramér's V = {r['effect_size']:.3f}로 관측된다.{detail}",
            "evidence": {"figure": res["figures"].get(f"rate_{r['feature']}", res["figures"].get("categorical_frequency")),
                         "function": "credit_risk.eda.categorical_tests", "sql": _source_sql(r["feature"])},
        })

    miss = res["missingness"].dropna(subset=["effect_size"])
    miss = miss[(miss["missing_ratio"] >= MIN_MISSING_SHARE) & (miss["missing_ratio"] <= 1 - MIN_MISSING_SHARE)]
    for _, r in miss.iterrows():
        kind = "이력 부재(structural NULL)" if r["structural"] else "결측"
        cands.append({
            "kind": "missingness", "feature": r["column"], "strength": r["effect_size"],
            "metric": "Cramér's V (phi)", "value": r["effect_size"],
            "title": f"{r['column']} {kind}와 관측 상환곤란율",
            "statement": (f"`{r['column']}` {kind} 고객({pct(r['missing_ratio'])})의 {RATE_NAME}은 {pct(r['rate_missing'])}, "
                          f"값이 있는 고객은 {pct(r['rate_present'])}로 관측된다 (phi = {r['effect_size']:.3f})."),
            "evidence": {"figure": res["figures"].get("missingness"), "function": "credit_risk.eda.missingness_vs_target",
                         "sql": f"SELECT ({r['column']} IS NULL) AS is_null, count(*), avg(target) FROM {MART} "
                                f"WHERE {TRAIN_FILTER} GROUP BY 1"},
        })

    exposure_cands = []
    for e in res["exposure"]:
        c = e["concentration"]
        if e["sensitive"] or not c["elevated_groups"]:
            continue
        gap = c["exposure_share"] - c["customer_share"]
        cmp = ("Exposure 비중이 고객 비중보다 크다" if gap > 0.005 else
               "Exposure 비중이 고객 비중보다 작다" if gap < -0.005 else "Exposure 비중과 고객 비중은 비슷한 수준이다")
        exposure_cands.append({
            "kind": "exposure_concentration", "feature": e["source"], "strength": c["exposure_share"],
            "metric": "exposure share of elevated-rate segments", "value": c["exposure_share"],
            "title": f"{e['name']} 기준 위험 상위 세그먼트의 Exposure 비중",
            "statement": (f"`{e['name']}` 기준 {RATE_NAME}이 전체의 {ELEVATED_RATIO}배 이상인 세그먼트"
                          f"({', '.join(map(str, c['elevated_groups'][:5]))})는 고객의 {pct(c['customer_share'])}, "
                          f"{EXPOSURE_NAME}의 {pct(c['exposure_share'])}, 관측 상환곤란 건의 {pct(c['event_share'])}를 차지한다. "
                          f"{cmp}."),
            "evidence": {"figure": e["figure"], "function": "credit_risk.eda.exposure_concentration",
                         "sql": _source_sql(e["source"], "amt_credit")},
            "powerbi_page": "Page 1. Executive Portfolio Overview / Page 2. Risk Segmentation",
        })
    tail = res.get("score_tail") or {}
    if tail:
        exposure_cands.append({
            "kind": "exposure_concentration", "feature": tail["score"], "strength": tail["exposure_share"],
            "metric": "exposure share of lowest-score tail", "value": tail["exposure_share"],
            "title": f"외부점수 하위 {tail['tail']:.0%} 고객의 Exposure·상환곤란 비중",
            "statement": (f"`{tail['score']}` 하위 {tail['tail']:.0%} 고객은 {EXPOSURE_NAME}의 {pct(tail['exposure_share'])}, "
                          f"관측 상환곤란 건의 {pct(tail['event_share'])}를 차지하며, 이 구간의 {RATE_NAME}은 "
                          f"{pct(tail['rate_tail'])} (나머지 {pct(tail['rate_rest'])})로 관측된다."),
            "evidence": {"figure": res["figures"].get("exposure_ext_source"),
                         "function": "credit_risk.eda.score_tail_concentration",
                         "sql": _source_sql(tail["score"], "amt_credit")},
            "powerbi_page": "Page 1. Executive Portfolio Overview / Page 2. Risk Segmentation",
        })

    # one insight per feature family; per-kind quotas keep the list diverse; exposure insights last
    cands.sort(key=lambda c: -c["strength"])
    chosen, families, per_kind = [], set(), {}
    n_stat = max_insights - min(2, len(exposure_cands))
    for relax in (False, True):
        for c in cands:
            if len(chosen) >= n_stat:
                break
            fam = feature_family(c["feature"])
            if fam in families or (not relax and per_kind.get(c["kind"], 0) >= INSIGHT_QUOTA[c["kind"]]):
                continue
            chosen.append(c)
            families.add(fam)
            per_kind[c["kind"]] = per_kind.get(c["kind"], 0) + 1
    chosen.sort(key=lambda c: -c["strength"])
    exposure_cands.sort(key=lambda c: -c["strength"])
    seen_exp = set()
    for c in exposure_cands:
        if len([x for x in chosen if x["kind"] == "exposure_concentration"]) >= 2:
            break
        if c["feature"] in seen_exp:
            continue
        seen_exp.add(c["feature"])
        chosen.append(c)
    chosen = chosen[:max_insights]
    if len(chosen) < min_insights:
        log.warning("only %d insights could be selected (min %d)", len(chosen), min_insights)

    for i, c in enumerate(chosen, 1):
        c["id"] = f"I{i:02d}"
        c["rank"] = i
        c.setdefault("powerbi_page", powerbi_page(c["feature"]))
        c["caveat"] = "연관(association) 관측이며 인과관계를 의미하지 않음. 학습 표본 밖으로 일반화하지 않음."
        if res.get("synthetic"):
            c["caveat"] += " 합성 테스트 데이터 기반 수치."
    return chosen


# ============================================================================================
# Report rendering
# ============================================================================================
def _banner(res: dict) -> str:
    if res.get("synthetic"):
        return (f"> **{SYNTHETIC_BANNER}**\n>\n> `raw.columns_description`의 설명이 `[synthetic]`으로 시작하여 "
                "합성 데이터로 자동 판별되었다. 아래 수치는 파이프라인 검증용이며 해석에 사용하지 않는다.\n\n")
    return ""


def _fig(path: str | None, alt: str) -> str:
    if not path:
        return ""
    rel = path.replace("reports/", "", 1)
    return f"![{alt}]({rel})\n\n`{path}`\n\n"


def render_eda_report(res: dict[str, Any]) -> str:
    bal = res["target_balance"]
    f = res["figures"]
    out: list[str] = [f"# EDA 보고서 — Credit Risk & Loan Portfolio\n\n{_banner(res)}"]
    out.append(
        f"- 생성 시각: {res['generated_at']} · 실행 시간: {res['runtime_seconds']:.1f}s\n"
        f"- 분석 대상: `{MART}`의 train 고객 {bal['n']:,}명 (1고객 1행), 보조 테이블은 SQL 집계 결과만 사용\n"
        f"- 용어: TARGET 평균 = **{RATE_NAME}** (실제 부도율·IFRS 9 부도율 아님), "
        f"AMT_CREDIT = **{EXPOSURE_NAME}**\n"
        "- 모든 관계는 연관(association)이며 인과(causation)를 의미하지 않는다. 학습 표본 결과를 실제 은행 "
        "전체 고객군에 일반화하지 않는다.\n"
        "- 성별·연령·가족 관련 속성은 **기술통계 목적**으로만 제시하며 승인 기준으로 사용하지 않는다 (spec §19).\n\n")

    # insights
    out.append("## 1. 핵심 인사이트 (Power BI 페이지 반영용)\n\n"
               "효과크기(|rank-biserial r|, Cramér's V) 또는 Exposure 집중도 순으로 자동 선정되었다. "
               "공정성 민감 속성은 후보에서 제외했다.\n\n")
    for ins in res["insights"]:
        ev = ins["evidence"]
        out.append(
            f"**{ins['id']}. {ins['title']}**  \n{ins['statement']}  \n"
            f"- 지표: {ins['metric']} = {num(ins['value'], 3)} · Power BI: {ins['powerbi_page']}\n"
            f"- 근거: 그림 `{ev.get('figure') or '-'}` · 함수 `{ev['function']}` · SQL `{ev['sql']}`\n"
            f"- 주의: {ins['caveat']}\n\n")

    # inventory
    inv = res["inventory"]
    out.append("## 2. 테이블 인벤토리 (행·열·용량·키·결측)\n\n"
               "행 수와 컬럼별 결측률은 PostgreSQL에서 `count(*)`/`count(col)`로 집계했다 (대형 원천 테이블을 pandas로 "
               "적재하지 않음). 키 중복은 최신 `audit.data_quality_result`의 `key_unique` 결과를 재사용하고, 없으면 SQL로 계산했다.\n\n")
    out.append(md_table(inv, {
        "table": ("table", None), "rows": ("rows", lambda v: f"{int(v):,}"), "columns": ("cols", lambda v: f"{int(v)}"),
        "size_mb": ("size (MB)", lambda v: f"{v:,.1f}"), "unique_key": ("key", lambda v: v or "-"),
        "key_duplicates": ("key dup", lambda v: "-" if v is None or pd.isna(v) else f"{int(v):,}"),
        "mean_null_ratio": ("mean null", pct),
        "cols_null_gt_warn": (f"cols null>{pct(res['null_warn'], 0)}", lambda v: f"{int(v)}"),
        "top_null_columns": ("top null columns", None)}))
    dq = res["dq_summary"]
    if len(dq):
        out.append("\n데이터 품질 점검 결과(audit, 최신 결과 기준): " + ", ".join(
            f"{r.layer}/{r.status} {int(r.checks)}건" for r in dq.itertuples()) + "\n\n")

    # target balance
    out.append(
        f"## 3. TARGET 불균형\n\n- TARGET=1: {bal['events']:,}명, TARGET=0: {bal['non_events']:,}명\n"
        f"- {RATE_NAME}: **{pct(bal['rate'], 2)}** (95% Wilson CI {pct(bal['ci_low'], 2)} – {pct(bal['ci_high'], 2)})\n"
        f"- 불균형 비율 (0:1) = {bal['imbalance_ratio']:.1f} : 1 → 모델링 시 accuracy 대신 ROC-AUC/PR-AUC, "
        "stratified split, class weight 검토 필요\n\n" + _fig(f.get("target_balance"), "TARGET balance"))

    # numeric
    npf = res["numeric_profile"]
    out.append("## 4. 주요 수치형 변수 분포와 극단값\n\n"
               "극단값 = Q1 − 3·IQR 미만 또는 Q3 + 3·IQR 초과(far outlier). 삭제하지 않고 분포 왜곡 정도만 기록한다.\n\n")
    out.append(md_table(npf, {
        "feature": ("feature", None), "missing_ratio": ("null", pct), "median": ("median", num),
        "p01": ("p1", num), "p99": ("p99", num), "max": ("max", num), "skew": ("skew", lambda v: num(v, 2)),
        "far_outlier_ratio": ("far outlier", lambda v: pct(v, 2))}))
    heavy = npf[npf["skew"].abs() >= 2].sort_values("skew", ascending=False)
    if len(heavy):
        out.append("\n왜도 |skew| ≥ 2인 변수: " + ", ".join(
            f"`{r.feature}` ({r.skew:.1f})" for r in heavy.itertuples()) + " → 금액형은 log 변환/robust 통계를 우선한다.\n")
    if res.get("employment_sentinel"):
        es = res["employment_sentinel"]
        out.append(f"\n`DAYS_EMPLOYED` sentinel(365243) 고객: {es['n']:,}명 ({pct(es['share'])}), "
                   f"이 그룹의 {RATE_NAME} {pct(es['rate'])} (나머지 {pct(es['rate_rest'])}). "
                   "`employment_years`에서는 결측 처리되고 `days_employed_anom` 플래그로 분리되어 있다.\n")
    out.append("\n" + _fig(f.get("numeric_distributions"), "numeric distributions"))

    # categorical
    cpf = res["categorical_profile"]
    out.append(f"## 5. 범주형 변수 빈도와 희소 범주\n\n희소 범주 = 고객 비중 {pct(RARE_SHARE, 0)} 미만.\n\n")
    out.append(md_table(cpf, {
        "feature": ("feature", lambda v: f"{v}" + (" ⚖️" if is_sensitive(v) else "")),
        "levels": ("levels", lambda v: f"{int(v)}"),
        "missing_ratio": ("null", pct), "top_level": ("top level", None), "top_share": ("top share", pct),
        "rare_levels": ("rare levels", lambda v: f"{int(v)}"), "rare_share_total": ("rare share", lambda v: pct(v, 2)),
        "rare_examples": ("rare examples", lambda v: v or "-")}))
    out.append("\n⚖️ = 공정성 민감 속성 (기술통계 전용).\n\n" + _fig(f.get("categorical_frequency"), "categorical frequency"))

    # target comparison
    tn = res["tests_numeric"]
    app = tn[tn["family"] == "application"].copy()
    out.append("## 6. TARGET별 소득·대출액·Annuity·비율 Feature 비교\n\n"
               "중앙값과 Mann–Whitney U의 rank-biserial r(양수 = TARGET=1에서 큼)을 보고한다. 상세 검정표는 "
               "`reports/statistical_analysis.md` 참조.\n\n")
    out.append(md_table(app.sort_values("effect_size", key=abs, ascending=False), {
        "feature": ("feature", lambda v: f"{v}" + (" ⚖️" if is_sensitive(v) else "")),
        "median1": ("median (T=1)", lambda v: num(v, 3)), "median0": ("median (T=0)", lambda v: num(v, 3)),
        "effect_size": ("rank-biserial r", lambda v: f"{v:+.3f}"),
        "effect_label": ("effect", lambda v: st.EFFECT_LABEL_KO.get(v, v))}))
    out.append("\n" + _fig(f.get("target_boxplots"), "boxplots by target") + _fig(f.get("effect_sizes"), "effect sizes"))

    # age / employment / ext
    out.append("## 7. 연령·고용기간·외부점수와 관측 상환곤란율\n\n")
    for b in res["binned"]:
        if b["name"] not in ("age_band", "employment_band", "ext_source_decile", "income_band"):
            continue
        note = " (⚖️ 공정성 민감 — 기술통계 전용, 승인 기준 사용 금지)" if b["sensitive"] else ""
        out.append(f"### {b['name']}{note}\n\nCramér's V = {num(b['test']['effect_size'], 3)}\n\n")
        out.append(_rate_md(b["table"]) + "\n" + _fig(f.get(f"rate_{b['name']}"), b["name"]))

    # aggregates
    agg = tn[tn["family"] == "aggregate"].copy()
    out.append("## 8. 보조 테이블 집계 Feature와 TARGET\n\n"
               "집계 Feature의 NULL은 해당 원천에 이력이 없음을 의미하므로 0과 구분한다. 수치 비교는 이력 보유 고객"
               "(non-NULL) 내에서 수행했고, 이력 보유 여부 자체는 아래 has_* 표로 따로 비교한다.\n\n")
    out.append(md_table(agg.sort_values("effect_size", key=abs, ascending=False), {
        "feature": ("feature", None), "coverage": ("이력 보유율", pct),
        "median1": ("median (T=1)", lambda v: num(v, 3)), "median0": ("median (T=0)", lambda v: num(v, 3)),
        "effect_size": ("rank-biserial r", lambda v: f"{v:+.3f}"),
        "effect_label": ("effect", lambda v: st.EFFECT_LABEL_KO.get(v, v))}))
    hist = res["history_flags"]
    if len(hist):
        out.append("\n**이력 보유 여부별 관측 상환곤란율**\n\n" + md_table(hist, {
            "flag": ("flag", None), "share_with": ("보유 비중", pct), "rate_with": ("보유 시 rate", pct),
            "rate_without": ("미보유 시 rate", pct), "effect_size": ("Cramér's V", lambda v: num(v, 3))}))
    for b in res["binned"]:
        if b["name"] in ("inst_late_ratio_band", "prev_refused_ratio_band", "bureau_worst_status"):
            out.append(f"\n### {b['name']}\n\nCramér's V = {num(b['test']['effect_size'], 3)}\n\n"
                       + _rate_md(b["table"]) + "\n" + _fig(f.get(f"rate_{b['name']}"), b["name"]))

    # missingness
    miss_all = res["missingness"].dropna(subset=["effect_size"])
    miss = miss_all[miss_all["missing_ratio"] >= MIN_MISSING_SHARE].sort_values("effect_size", ascending=False)
    out.append("## 9. 결측 자체와 TARGET의 관계\n\n"
               f"mart 컬럼 {len(res['missingness'])}개 중 결측이 존재하는 {len(miss_all)}개에 대해 NULL 여부와 TARGET의 "
               "2x2 교차표 Chi-square(희소 시 Fisher)를 SQL 집계 결과로 계산했다. 아래 표는 결측 비율 "
               f"{pct(MIN_MISSING_SHARE, 0)} 이상인 {len(miss)}개 컬럼 중 효과크기 상위 20개다 (소표본 결측의 극단 비율 배제). "
               "`structural`=집계 원천 이력 부재.\n\n")
    out.append(md_table(miss.head(20), {
        "column": ("column", None), "n_missing": ("n (NULL)", lambda v: f"{int(v):,}"),
        "missing_ratio": ("null 비율", pct), "structural": ("structural", lambda v: "Y" if v else ""),
        "rate_missing": ("rate (NULL)", pct), "rate_present": ("rate (값 있음)", pct),
        "effect_size": ("phi / V", lambda v: num(v, 3))}))
    out.append("\n" + _fig(f.get("missingness"), "missingness vs target"))

    # exposure
    pc = res["portfolio_concentration"]
    out.append(f"## 10. {EXPOSURE_NAME} 기준 위험 집중도\n\n"
               f"- 총 Exposure Proxy: {num(pc.get('total_exposure'))}, 고객 중앙값 {num(pc.get('median_exposure'))}\n"
               f"- 상위 10% 고객의 Exposure 비중 {pct(pc.get('top10_share'))}, 상위 1% {pct(pc.get('top1_share'))}, "
               f"Gini {num(pc.get('gini'), 3)}\n")
    tail = res.get("score_tail") or {}
    if tail:
        out.append(f"- `{tail['score']}` 하위 {tail['tail']:.0%}(≤ {tail['cutoff']:.3f}) 고객: Exposure 비중 "
                   f"{pct(tail['exposure_share'])}, 관측 상환곤란 건 비중 {pct(tail['event_share'])}, rate "
                   f"{pct(tail['rate_tail'])} vs 나머지 {pct(tail['rate_rest'])}\n")
    out.append(f"\n세그먼트별로 {RATE_NAME}이 전체의 {ELEVATED_RATIO}배 이상이고 n ≥ {res['min_segment_n']:,}인 구간을 "
               "'elevated'로 정의해 고객 비중과 Exposure 비중을 비교한다.\n\n")
    rows = []
    for e in res["exposure"]:
        c = e["concentration"]
        rows.append({"segment": e["name"] + (" ⚖️" if e["sensitive"] else ""),
                     "groups": ", ".join(map(str, c["elevated_groups"][:6])) or "-",
                     "customer_share": c["customer_share"], "exposure_share": c["exposure_share"],
                     "event_share": c["event_share"], "rate": c["rate"]})
    out.append(md_table(pd.DataFrame(rows), {
        "segment": ("segment", None), "groups": ("elevated groups", None), "customer_share": ("고객 비중", pct),
        "exposure_share": ("Exposure 비중", pct), "event_share": ("상환곤란 건 비중", pct), "rate": ("rate", pct)}))
    for e in res["exposure"]:
        if e.get("figure"):
            out.append("\n" + _fig(e["figure"], e["name"]))

    # correlation
    pairs = res["corr_pairs"]
    out.append(f"## 11. 상관관계·중복 Feature 점검\n\n"
               f"mart 수치형 컬럼 {res['corr_n_features']}개를 md5 정렬 결정적 표본 {res['corr_sample_n']:,}행으로 "
               f"Pearson 상관을 계산했다. |r| > {CORR_THRESHOLD} 쌍: **{len(pairs)}개**. 모델링 단계에서 중복 Feature "
               "제거 또는 정규화(L2/트리 모델) 근거로 사용한다.\n\n")
    out.append(md_table(pairs.head(30), {
        "feature_a": ("feature A", None), "feature_b": ("feature B", None), "r": ("r", lambda v: f"{v:+.3f}")}))
    if len(pairs) > 30:
        out.append(f"\n… 외 {len(pairs) - 30}쌍 (`reports/eda_results.json`의 `corr_pairs`).\n")
    vif = res["vif"]
    if len(vif):
        out.append("\n**다중공선성 참고 — VIF (주요 신청 Feature, 완전 관측 행 기준)**\n\n" + md_table(vif, {
            "feature": ("feature", None), "vif": ("VIF", lambda v: num(v, 1))}) +
            "\nVIF > 10은 강한 다중공선성 신호로, 선형 모델 해석 시 주의한다.\n")
    out.append("\n" + _fig(f.get("correlation"), "correlation heatmap"))

    out.append("## 12. 공정성·윤리 고지\n\n"
               "- 성별(code_gender), 연령(age_years/age_band), 가족 상태(name_family_status), 자녀·가구원 수는 기술통계로만 "
               "제시했으며 핵심 인사이트 후보에서 제외했다. 이 속성들을 승인 자동화 근거로 사용하지 않는다.\n"
               "- 본 분석은 교육·포트폴리오 목적이며, 실제 금융 의사결정에는 법률·규제·공정성 검토와 독립 검증이 필요하다.\n"
               "- 관측된 연관은 인과관계가 아니다.\n")
    return "".join(out)


def _rate_md(table: pd.DataFrame) -> str:
    return md_table(table, {
        "group": ("구간", lambda v: str(v)), "n": ("n", lambda v: f"{int(v):,}"),
        "events": ("TARGET=1", lambda v: f"{int(v):,}"), "rate": ("rate", lambda v: pct(v, 2)),
        "ci_low": ("95% CI low", lambda v: pct(v, 2)), "ci_high": ("95% CI high", lambda v: pct(v, 2)),
        "customer_share": ("고객 비중", pct), "exposure_share": ("Exposure 비중", pct)})


def render_stats_report(res: dict[str, Any]) -> str:
    bal = res["target_balance"]
    tests = res["tests_all"]
    sig = tests[tests["p_adj_bh"] < 0.05]
    negl = sig[sig["effect_label"] == "negligible"]
    out = [f"# 통계 분석 보고서 — TARGET 연관성 검정\n\n{_banner(res)}"]
    out.append(
        "> **대표본 경고**: 분석 표본은 train 고객 "
        f"{bal['n']:,}명이다. 실데이터(약 30만 행) 규모에서는 실무적으로 의미 없는 아주 작은 차이도 p-value가 "
        "매우 작아진다. 따라서 **결론은 효과크기(rank-biserial r, Cramér's V, Cohen's d)와 구간별 관측 상환곤란율 차이로 "
        f"판단**하며, p-value는 보조 정보로만 사용한다. 이번 실행에서 BH 보정 후 유의한 {len(sig)}개 검정 중 "
        f"{len(negl)}개는 효과크기가 '무시할 수준'이다.\n\n")
    out.append(
        "## 방법\n\n"
        "- **수치형**: Mann–Whitney U (양측, 정규근사) + rank-biserial r = 2U₁/(n₁n₀) − 1 (양수 = TARGET=1 쪽 값이 큼). "
        "분포 왜도 |skew| < 2인 변수에 한해 Welch t-test + Cohen's d를 참고로 병기 (금액형 heavy-tail 변수는 MWU만 사용).\n"
        "- **범주형/구간형**: Pearson Chi-square 독립성 검정 + Cramér's V. 2x2 표에서 기대빈도 < 5이면 Fisher exact test.\n"
        f"- **구간별 {RATE_NAME}**: Wilson score 95% 신뢰구간.\n"
        f"- **다중 비교**: 아래 {len(tests)}개 검정 전체를 하나의 family로 보고 **Benjamini–Hochberg (FDR)** 보정 "
        "p-value(`p_adj (BH)`)를 사용한다. 보수적 기준인 **Bonferroni** 보정값도 함께 제시한다. Welch t-test는 MWU의 "
        "참고값이므로 family에 포함하지 않았다.\n"
        "- 효과크기 라벨: |r|·V < 0.1 무시할 수준, < 0.3 작음, < 0.5 중간, 그 이상 큼 (Cohen's d는 0.2/0.5/0.8).\n"
        "- ⚖️ 표시는 공정성 민감 속성으로 **기술통계 전용**이며 승인 기준으로 사용하지 않는다.\n\n")

    def interp_col(df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        d["interpretation"] = [st.interpret(r) for _, r in d.iterrows()]
        return d

    feat_fmt = ("feature", lambda v: f"{v}" + (" ⚖️" if is_sensitive(v) else ""))
    num_cols = {
        "feature": feat_fmt, "n1": ("n (T=1)", lambda v: f"{int(v):,}"), "n0": ("n (T=0)", lambda v: f"{int(v):,}"),
        "statistic": ("U", lambda v: f"{v:,.0f}"), "p_value": ("p", pval), "p_adj_bh": ("p_adj (BH)", pval),
        "p_adj_bonferroni": ("p_adj (Bonf.)", pval), "effect_size": ("rank-biserial r", lambda v: f"{v:+.3f}"),
        "welch_t": ("Welch t", lambda v: num(v, 2)), "welch_p": ("Welch p", pval),
        "cohens_d": ("Cohen's d", lambda v: "n/a" if not np.isfinite(v) else f"{v:+.3f}"),
        "interpretation": ("해석", None)}
    tn = tests[tests["family"].isin(["application", "aggregate"])]
    for fam, title in (("application", "1. 수치형 신청 Feature (TARGET=1 vs 0)"),
                       ("aggregate", "2. 보조 테이블 집계 Feature (이력 보유 고객 내 비교)")):
        part = interp_col(tn[tn["family"] == fam]).sort_values("effect_size", key=abs, ascending=False)
        out.append(f"## {title}\n\n" + md_table(part, num_cols) + "\n")

    cat_cols = {
        "feature": feat_fmt, "levels": ("levels", lambda v: num(v, 0)), "test": ("test", None),
        "statistic": ("χ² / OR", lambda v: num(v, 2)), "dof": ("dof", lambda v: num(v, 0)),
        "p_value": ("p", pval), "p_adj_bh": ("p_adj (BH)", pval), "p_adj_bonferroni": ("p_adj (Bonf.)", pval),
        "effect_size": ("Cramér's V", lambda v: num(v, 3)), "interpretation": ("해석", None)}
    for fam, title in (("categorical", "3. 범주형 Feature — Chi-square / Cramér's V"),
                       ("binned", "4. 구간화 Feature — Chi-square / Cramér's V"),
                       ("history_flag", "5. 이력 보유 여부 (has_*) — 2x2 검정"),
                       ("missingness", "6. 결측 여부 (NULL indicator) — 2x2 검정, 효과크기 상위 25개")):
        part = interp_col(tests[tests["family"] == fam]).sort_values("effect_size", ascending=False)
        if fam == "missingness":
            part = part.head(25)
        out.append(f"## {title}\n\n" + md_table(part, cat_cols) + "\n")

    out.append(f"## 7. 구간별 {RATE_NAME}과 95% Wilson 신뢰구간\n\n")
    for b in res["binned"]:
        note = " ⚖️" if b["sensitive"] else ""
        out.append(f"### {b['name']}{note} (source: `{b['source']}`)\n\n" + _rate_md(b["table"]) + "\n")
    for name, rt in res["categorical_rates"].items():
        note = " ⚖️" if is_sensitive(name) else ""
        out.append(f"### {name}{note}\n\n" + _rate_md(rt.sort_values("n", ascending=False).head(20)) + "\n")

    out.append("## 8. 해석 원칙\n\n"
               "- 효과크기가 '무시할 수준'인 결과는 p-value와 무관하게 실무적 의미를 주장하지 않는다.\n"
               "- 모든 결과는 연관(association)이며, 해당 Feature가 상환곤란을 유발한다는 인과 해석을 하지 않는다.\n"
               "- 신뢰구간이 넓은 소표본 구간(n이 작은 범주)은 순위 비교에 사용하지 않는다.\n"
               "- 공정성 민감 속성의 차이는 모니터링 목적의 기술통계이며 승인 기준이 아니다.\n")
    return "".join(out)


# ============================================================================================
# Orchestration
# ============================================================================================
def compute_eda(conn: psycopg.Connection, settings: Settings, fig_dir: Path) -> dict[str, Any]:
    """Run every analysis and draw figures; returns the full results dict (DataFrames inside)."""
    proj = settings.project
    res: dict[str, Any] = {"figures": {}, "null_warn": float(proj["quality"]["null_ratio_warn"])}
    figs = res["figures"]

    with timed("eda: data source + inventory"):
        res["synthetic"] = detect_synthetic(conn)
        res["inventory"], res["column_nulls"] = table_inventory(conn, settings)
        res["dq_summary"] = dq_status_summary(conn)

    mart_cols = table_columns(conn, *MART.split("."))
    available = set(mart_cols["column_name"])
    if "target" not in available:
        raise RuntimeError(f"{MART} has no target column — run build-features first")
    pick = lambda cols: [c for c in cols if c in available]  # noqa: E731
    app_num, agg_num, cats, flags = pick(APP_NUMERIC), pick(AGG_NUMERIC), pick(CATEGORICAL), pick(HISTORY_FLAGS)
    exposure_col = str(proj["risk"]["exposure_proxy_column"]).lower()
    load_cols = list(dict.fromkeys(pick(EXTRA) + [exposure_col] + app_num + agg_num + cats + flags))

    with timed("eda: load customer frame"):
        df = load_train_frame(conn, load_cols)
    if exposure_col != "amt_credit":
        df["amt_credit"] = df[exposure_col]
    log.info("eda frame: %s rows x %s cols (%.1f MB)", len(df), df.shape[1], df.memory_usage(deep=True).sum() / 1e6)

    bal = target_balance(df)
    res["target_balance"] = bal
    overall = bal["rate"]
    res["min_segment_n"] = int(max(30, 0.005 * len(df)))
    figs["target_balance"] = plot_target_balance(bal, fig_dir / "eda_target_balance.png")

    with timed("eda: distributions"):
        res["numeric_profile"] = numeric_profile(df, app_num + agg_num)
        res["categorical_profile"] = categorical_profile(df, cats)
        if "days_employed_anom" in df:
            anom = df["days_employed_anom"] == 1
            res["employment_sentinel"] = {"n": int(anom.sum()), "share": float(anom.mean()),
                                          "rate": float(df.loc[anom, "target"].mean()) if anom.any() else np.nan,
                                          "rate_rest": float(df.loc[~anom, "target"].mean())}
        figs["numeric_distributions"] = plot_numeric_distributions(df, fig_dir / "eda_numeric_distributions.png")
        figs["categorical_frequency"] = plot_categorical_frequency(
            df, ["name_income_type", "name_education_type", "occupation_type"], fig_dir / "eda_categorical_frequency.png")

    with timed("eda: target comparisons"):
        t_app = target_comparison(df, app_num, "application")
        t_agg = target_comparison(df, agg_num, "aggregate")
        t_cat = categorical_tests(df, cats, "categorical")
        figs["target_boxplots"] = plot_target_boxplots(
            df, ["amt_income_total", "amt_credit", "amt_annuity", "credit_income_ratio", "annuity_income_ratio",
                 "credit_annuity_ratio"], fig_dir / "eda_target_boxplots.png")

    with timed("eda: binned risk"):
        seg = proj["segments"]
        bands: list[tuple[str, str, pd.Series]] = []
        if "age_years" in df:
            df["age_band"] = age_band(df["age_years"], seg["age_bins"])
            bands.append(("age_band", "age_years", df["age_band"]))
        if "amt_income_total" in df:
            df["income_band"] = income_band(df["amt_income_total"], seg["income_bins"], seg["income_labels"])
            bands.append(("income_band", "amt_income_total", df["income_band"]))
        if "employment_years" in df:
            anom = df["days_employed_anom"] if "days_employed_anom" in df else pd.Series(0, index=df.index)
            bands.append(("employment_band", "employment_years", employment_band(df["employment_years"], anom)))
        if "ext_source_mean" in df:
            df["ext_source_decile"] = quantile_band(df["ext_source_mean"], 10, "D")
            df["ext_source_quintile"] = quantile_band(df["ext_source_mean"], 5, "Q")
            bands.append(("ext_source_decile", "ext_source_mean", df["ext_source_decile"]))
        if "inst_late_ratio" in df:
            bands.append(("inst_late_ratio_band", "inst_late_ratio", ratio_band(df["inst_late_ratio"], [0.05, 0.2])))
        if "prev_refused_ratio" in df:
            bands.append(("prev_refused_ratio_band", "prev_refused_ratio",
                          ratio_band(df["prev_refused_ratio"], [0.25, 0.5])))
        if "bur_bb_worst_status" in df:
            bands.append(("bureau_worst_status", "bur_bb_worst_status", status_band(df["bur_bb_worst_status"])))
        res["binned"] = []
        for name, source, band in bands:
            b = binned_risk(df, band, name, source)
            b["source"] = source
            b["sensitive"] = is_sensitive(name) or is_sensitive(source)
            res["binned"].append(b)
            figs[f"rate_{name}"] = plot_rate_bars(
                b["table"][b["table"]["n"] >= res["min_segment_n"]], overall, f"Observed Difficulty Rate by {name}" + (" (descriptive only)" if b["sensitive"] else ""),
                source, fig_dir / f"eda_rate_{name}.png")

        res["categorical_rates"] = {}
        for c in ["name_income_type", "name_education_type", "occupation_type", "name_contract_type",
                  "name_housing_type", "organization_type", "code_gender", "name_family_status", "prev_last_status"]:
            if c in df:
                rt = st.rate_table(df[c], df["target"], df["amt_credit"])
                res["categorical_rates"][c] = rt
        for c in ["name_income_type", "occupation_type", "name_education_type"]:
            if c in res["categorical_rates"]:
                rt = res["categorical_rates"][c]
                rt = rt[rt["n"] >= res["min_segment_n"]].sort_values("rate", ascending=False)
                figs[f"rate_{c}"] = plot_rate_bars(rt, overall, f"Observed Difficulty Rate by {c}", c,
                                                   fig_dir / f"eda_rate_{c}.png")

        hist_rows, hist_tests = [], []
        for flag in flags:
            s = df[flag].fillna(0)
            res_t = st.crosstab_test(s, df["target"])
            hist_tests.append({"family": "history_flag", "feature": flag, "sensitive": False,
                               "n1": int(df["target"].sum()), "n0": int((df["target"] == 0).sum()), **res_t})
            hist_rows.append({"flag": flag, "share_with": float((s == 1).mean()),
                              "rate_with": float(df.loc[s == 1, "target"].mean()) if (s == 1).any() else np.nan,
                              "rate_without": float(df.loc[s != 1, "target"].mean()) if (s != 1).any() else np.nan,
                              "effect_size": res_t["effect_size"]})
        res["history_flags"] = pd.DataFrame(hist_rows)

    with timed("eda: missingness vs target (SQL)"):
        skip = {"sk_id_curr", "source", "target", "feature_version", "built_at"}
        miss = missingness_vs_target(conn, [c for c in mart_cols["column_name"] if c not in skip])
        res["missingness"] = miss
        figs["missingness"] = plot_missingness(miss, overall, fig_dir / "eda_missingness_vs_target.png")

    with timed("eda: exposure concentration"):
        res["portfolio_concentration"] = portfolio_concentration(df["amt_credit"])
        res["score_tail"] = score_tail_concentration(df) if "ext_source_mean" in df else {}
        exp_specs = [("income_band", "amt_income_total"), ("ext_source_quintile", "ext_source_mean"),
                     ("name_income_type", "name_income_type"), ("occupation_type", "occupation_type"),
                     ("name_education_type", "name_education_type"), ("name_housing_type", "name_housing_type"),
                     ("name_contract_type", "name_contract_type"), ("age_band", "age_years")]
        res["exposure"] = []
        for name, source in exp_specs:
            if name not in df:
                continue
            rt = st.rate_table(df[name], df["target"], df["amt_credit"])
            conc = exposure_concentration(rt, overall, res["min_segment_n"])
            sensitive = is_sensitive(name) or is_sensitive(source)
            entry = {"name": name, "source": source, "sensitive": sensitive, "table": rt, "concentration": conc,
                     "figure": None}
            if name in ("income_band", "ext_source_quintile", "name_income_type"):
                plot_t = rt[rt["n"] >= res["min_segment_n"]]
                entry["figure"] = plot_exposure(plot_t, overall, f"Customer vs exposure share by {name}",
                                                fig_dir / f"eda_exposure_{name}.png")
                key = "exposure_ext_source" if name == "ext_source_quintile" else f"exposure_{name}"
                figs[key] = entry["figure"]
            res["exposure"].append(entry)

    with timed("eda: correlation"):
        numeric_types = {"double precision", "bigint", "integer", "smallint", "numeric", "real"}
        num_cols = [c for c, t in zip(mart_cols["column_name"], mart_cols["data_type"], strict=True)
                    if t in numeric_types and c not in skip]
        sample = load_numeric_sample(conn, num_cols, CORR_SAMPLE)
        corr, pairs = correlation_check(sample)
        res["corr_pairs"] = pairs
        res["corr_n_features"] = int(corr.shape[0])
        res["corr_sample_n"] = int(len(sample))
        heat_cols = [c for c in ["amt_income_total", "amt_credit", "amt_annuity", "amt_goods_price",
                                 "credit_income_ratio", "annuity_income_ratio", "credit_annuity_ratio", "age_years",
                                 "employment_years", "ext_source_1", "ext_source_2", "ext_source_3", "ext_source_mean",
                                 "cnt_children", "cnt_fam_members", "bur_active_count", "bur_active_debt_credit_ratio",
                                 "prev_refused_ratio", "inst_late_ratio", "inst_dpd_max", "pos_dpd_max",
                                 "cc_utilization_mean"] if c in corr.columns]
        figs["correlation"] = plot_correlation(corr, heat_cols, fig_dir / "eda_correlation_heatmap.png")
        res["vif"] = vif_table(sample, ["amt_income_total", "amt_credit", "amt_annuity", "amt_goods_price",
                                        "credit_income_ratio", "annuity_income_ratio", "credit_annuity_ratio",
                                        "age_years", "ext_source_2", "ext_source_mean", "region_rating_client"])

    # multiple-comparison family
    bin_tests = pd.DataFrame([{"family": "binned", "feature": b["name"], "sensitive": b["sensitive"],
                               **b["test"]} for b in res["binned"]])
    miss_tests = miss.dropna(subset=["p_value"]).rename(columns={"column": "feature"}).assign(
        family="missingness", sensitive=lambda d: d["feature"].map(is_sensitive), levels=2, dof=1)
    family = pd.concat([t_app, t_agg, t_cat, bin_tests, pd.DataFrame(hist_tests),
                        miss_tests[["family", "feature", "sensitive", "test", "statistic", "p_value", "effect_size",
                                    "effect_label", "levels", "dof"]]], ignore_index=True)
    family = st.adjust_results(family)
    res["tests_all"] = family
    res["tests_numeric"] = family[family["family"].isin(["application", "aggregate"])].reset_index(drop=True)
    res["tests_categorical"] = family[family["family"] == "categorical"].reset_index(drop=True)
    figs["effect_sizes"] = plot_effect_sizes(res["tests_numeric"], fig_dir / "eda_effect_sizes.png")
    return res


def results_payload(res: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly subset of the results (all numbers used in the reports)."""
    binned = [{"name": b["name"], "source": b["source"], "sensitive": b["sensitive"], "test": b["test"],
               "table": b["table"]} for b in res["binned"]]
    exposure = [{k: e[k] for k in ("name", "source", "sensitive", "concentration", "figure", "table")}
                for e in res["exposure"]]
    payload = {
        "generated_at": res["generated_at"], "runtime_seconds": res["runtime_seconds"],
        "data_source": {"synthetic": res["synthetic"], "banner": SYNTHETIC_BANNER if res["synthetic"] else None},
        "terminology": {"target_rate": RATE_NAME, "exposure": EXPOSURE_NAME},
        "insights": res["insights"],
        "target_balance": res["target_balance"], "inventory": res["inventory"], "dq_summary": res["dq_summary"],
        "numeric_profile": res["numeric_profile"], "categorical_profile": res["categorical_profile"],
        "employment_sentinel": res.get("employment_sentinel"),
        "tests": res["tests_all"], "binned": binned, "categorical_rates": res["categorical_rates"],
        "history_flags": res["history_flags"], "missingness": res["missingness"],
        "portfolio_concentration": res["portfolio_concentration"], "score_tail": res["score_tail"],
        "exposure": exposure, "corr_pairs": res["corr_pairs"], "corr_n_features": res["corr_n_features"],
        "corr_sample_n": res["corr_sample_n"], "vif": res["vif"], "figures": res["figures"],
        "min_segment_n": res["min_segment_n"],
        "fairness_note": "성별·연령·가족 속성은 기술통계 전용이며 승인 기준으로 사용하지 않음 (spec §19).",
    }
    return to_jsonable(payload)


def run_eda(settings: Settings) -> dict[str, Any]:
    """Entry point for `python -m credit_risk.cli eda`."""
    start = time.perf_counter()
    reports = settings.reports_dir
    fig_dir = reports / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings) as conn:
        res = compute_eda(conn, settings, fig_dir)
    res["generated_at"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    res["insights"] = select_insights(res)
    res["runtime_seconds"] = time.perf_counter() - start

    (reports / "eda_report.md").write_text(render_eda_report(res), encoding="utf-8")
    (reports / "statistical_analysis.md").write_text(render_stats_report(res), encoding="utf-8")
    payload = results_payload(res)
    write_json(reports / "eda_results.json", payload)
    log.info("eda finished in %.1fs: %d insights, %d tests, synthetic=%s -> %s",
             res["runtime_seconds"], len(res["insights"]), len(res["tests_all"]), res["synthetic"], _rel(reports))
    return payload
