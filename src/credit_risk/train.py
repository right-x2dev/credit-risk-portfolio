"""PD model development (spec §12, Phase 5).

Flow (holdout is touched exactly once, after every decision is frozen):
  1. load labelled customers + frozen split; select allowed features; automatic leakage check
  2. fit Baseline 0 (mean PD), Baseline 1 (Logistic Regression), Champion candidate (LightGBM)
     — all preprocessing lives inside sklearn Pipelines fitted on the TRAIN split only
     — LightGBM early stopping uses an inner slice of TRAIN, never validation/holdout
  3. choose champion on VALIDATION ROC-AUC
  4. compare Platt vs isotonic calibration by CV inside VALIDATION, fit the chosen one on VALIDATION
  5. freeze the review threshold on VALIDATION (review-capacity policy)
  6. evaluate once on HOLDOUT, persist metrics/curves/calibration/lift/segment tables + artifacts
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin, clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from .calibrate import CALIBRATORS, PDModel, compare_calibrators
from .config import PROJECT_ROOT, Settings
from .db import connect, read_frame, write_frame
from .evaluate import calibration_table, core_metrics, curve_points, group_performance, lift_table
from .features import assert_no_leakage, downcast, select_features
from .utils import audited_run, timed, write_json
from .validate import QualityRunner

log = logging.getLogger(__name__)
SPLITS = ("train", "validation", "holdout")


# ----------------------------------------------------------------------------- transformers / pipelines
class QuantileClipper(BaseEstimator, TransformerMixin):
    """Winsorise numeric columns at train-fitted quantiles (limits extreme ratios for the linear model)."""

    def __init__(self, lower: float = 0.01, upper: float = 0.99):
        self.lower = lower
        self.upper = upper

    def fit(self, X, y=None):
        arr = np.asarray(X, dtype=float)
        self.low_ = np.nanquantile(arr, self.lower, axis=0)
        self.high_ = np.nanquantile(arr, self.upper, axis=0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X, dtype=float), self.low_, self.high_)

    def get_feature_names_out(self, input_features=None):
        return np.asarray(input_features, dtype=object)


def build_lr_pipeline(numeric: list[str], categorical: list[str], cfg: dict, seed: int) -> Pipeline:
    pre = ColumnTransformer([
        ("num", Pipeline([("clip", QuantileClipper()),
                          ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                          ("scale", StandardScaler())]), numeric),
        ("cat", Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="MISSING")),
                          ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=0.005,
                                                   sparse_output=False))]), categorical),
    ], verbose_feature_names_out=True)
    clf = LogisticRegression(C=cfg["C"], max_iter=cfg["max_iter"], class_weight=cfg.get("class_weight"),
                             random_state=seed)
    return Pipeline([("pre", pre), ("clf", clf)])


def build_tree_preprocessor(numeric: list[str], categorical: list[str]) -> ColumnTransformer:
    return ColumnTransformer([
        ("num", "passthrough", numeric),
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1,
                               dtype=np.float32), categorical),
    ], verbose_feature_names_out=False)


def lgbm_params(cfg: dict, seed: int) -> dict:
    params = {k: v for k, v in cfg.items() if k != "early_stopping_rounds"}
    params["random_state"] = seed
    return params


def fit_lgbm(X_train: pd.DataFrame, y_train: np.ndarray, numeric: list[str], categorical: list[str],
             cfg: dict, seed: int) -> tuple[Pipeline, int]:
    """Find the number of trees by early stopping on an inner 10% slice of TRAIN, then refit on all TRAIN."""
    cat_idx = list(range(len(numeric), len(numeric) + len(categorical)))
    X_in, X_es, y_in, y_es = train_test_split(X_train, y_train, test_size=0.1, stratify=y_train, random_state=seed)
    pre = build_tree_preprocessor(numeric, categorical).fit(X_in)
    probe = lgb.LGBMClassifier(**lgbm_params(cfg, seed))
    probe.fit(pre.transform(X_in), y_in, eval_X=pre.transform(X_es), eval_y=y_es, eval_metric="auc",
              categorical_feature=cat_idx,
              callbacks=[lgb.early_stopping(cfg["early_stopping_rounds"], verbose=False)])
    best_iter = int(probe.best_iteration_ or cfg["n_estimators"])
    final_params = lgbm_params(cfg, seed) | {"n_estimators": best_iter}
    pipe = Pipeline([("pre", build_tree_preprocessor(numeric, categorical)),
                     ("clf", lgb.LGBMClassifier(**final_params))])
    pipe.fit(X_train, y_train, clf__categorical_feature=cat_idx)
    return pipe, best_iter


class ConstantModel(BaseEstimator):
    """Baseline 0: every customer gets the TRAIN observed rate."""

    def fit(self, X, y):
        self.rate_ = float(np.mean(y))
        return self

    def predict_proba(self, X):
        n = len(X)
        return np.column_stack([np.full(n, 1 - self.rate_), np.full(n, self.rate_)])


# ----------------------------------------------------------------------------- data
def add_segments(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    bins = settings.project["segments"]["age_bins"]
    labels = [f"{bins[i]}-{bins[i + 1] - 1}" if i < len(bins) - 2 else f"{bins[i]}+" for i in range(len(bins) - 1)]
    df["age_band"] = pd.cut(df["age_years"], bins=bins, right=False, labels=labels).astype(str)
    return df


def load_model_frame(settings: Settings) -> pd.DataFrame:
    with connect(settings) as conn, timed("load model frame"):
        df = read_frame(conn, """SELECT m.*, s.split FROM analytics.customer_feature_mart m
                                   JOIN analytics.model_split s USING (sk_id_curr)
                                  WHERE m.source = 'train'""")
    return downcast(df)


# ----------------------------------------------------------------------------- main
def train_and_evaluate(settings: Settings) -> dict:
    mcfg = settings.model
    seed = settings.random_seed
    exposure_col = settings.project["risk"]["exposure_proxy_column"].lower()
    df = load_model_frame(settings)
    numeric, categorical = select_features(df.drop(columns=["split"]), settings)
    features = numeric + categorical
    parts = {s: df[df["split"] == s].reset_index(drop=True) for s in SPLITS}
    X = {s: parts[s][features] for s in SPLITS}
    y = {s: parts[s]["target"].to_numpy().astype(int) for s in SPLITS}
    e = {s: parts[s][exposure_col].to_numpy().astype(float) for s in SPLITS}
    leakage_report = assert_no_leakage(features, settings, X["train"][numeric], pd.Series(y["train"]))
    log.info("features: %d numeric + %d categorical; leakage check %s", len(numeric), len(categorical), leakage_report)

    # ---------------- fit candidates (TRAIN only)
    timings = {}
    t0 = time.perf_counter()
    base0 = ConstantModel().fit(X["train"], y["train"])
    timings["baseline_mean"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    with timed("logistic regression"):
        lr = build_lr_pipeline(numeric, categorical, mcfg["models"]["logistic_regression"], seed)
        lr.fit(X["train"], y["train"])
    timings["logistic_regression"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    with timed("lightgbm"):
        gbm, best_iter = fit_lgbm(X["train"], y["train"], numeric, categorical, mcfg["models"]["lightgbm"], seed)
    timings["lightgbm"] = time.perf_counter() - t0
    candidates = {"baseline_mean": base0, "logistic_regression": lr, "lightgbm": gbm}

    # ---------------- selection on VALIDATION only
    val_scores = {name: m.predict_proba(X["validation"])[:, 1] for name, m in candidates.items()}
    val_metrics = {name: core_metrics(y["validation"], p, e["validation"]) for name, p in val_scores.items()}
    primary = mcfg["selection"]["primary_metric"]
    ranked = sorted(["logistic_regression", "lightgbm"], key=lambda n: val_metrics[n][primary], reverse=True)
    champion_name = ranked[0]
    improvement = val_metrics[champion_name][primary] - val_metrics["logistic_regression"][primary]
    log.info("validation %s: %s -> champion %s", primary,
             {n: round(m[primary], 4) for n, m in val_metrics.items() if n != "baseline_mean"}, champion_name)
    champion = candidates[champion_name]

    # ---------------- calibration (VALIDATION, cross-validated comparison)
    ccfg = mcfg["calibration"]
    cal_cmp = compare_calibrators(val_scores[champion_name], y["validation"], ccfg["methods"], ccfg["cv_folds"], seed)
    best_method = cal_cmp[cal_cmp["method"] != "none"].sort_values("cv_" + ccfg["selection_metric"]).iloc[0]["method"]
    calibrator = CALIBRATORS[best_method]().fit(val_scores[champion_name], y["validation"])
    log.info("calibration comparison:\n%s\n-> %s", cal_cmp.round(5).to_string(index=False), best_method)

    algo = "lgbm" if champion_name == "lightgbm" else "logreg"
    from .calibrate import schema_hash

    version = f"pd-{algo}-{settings.feature_version}-{schema_hash(numeric, categorical)[:8]}-s{seed}"
    risk = settings.project["risk"]
    pd_model = PDModel(model_version=version, algorithm=champion_name, pipeline=champion, calibrator=calibrator,
                       numeric_features=numeric, categorical_features=categorical,
                       pd_clip=tuple(risk["pd_clip"]),
                       metadata={"best_iteration": best_iter if champion_name == "lightgbm" else None,
                                 "calibration_method": best_method, "split_seed": seed})

    # ---------------- threshold frozen on VALIDATION
    _, val_pd = pd_model.predict_pd(parts["validation"])
    tcfg = mcfg["threshold"]
    threshold = float(np.quantile(val_pd, 1 - tcfg["review_capacity_share"]))
    log.info("threshold (top %.0f%% review capacity on validation) = %.4f", 100 * tcfg["review_capacity_share"], threshold)

    # ---------------- final evaluation incl. HOLDOUT (single pass, no further decisions)
    preds = {s: pd_model.predict_pd(parts[s]) for s in SPLITS}
    metric_rows, curve_rows, calib_rows, lift_rows = [], [], [], []

    def record(model_name: str, split: str, p: np.ndarray, kind: str) -> None:
        m = core_metrics(y[split], p, e[split], threshold if kind == "calibrated" else None)
        for k, v in m.items():
            metric_rows.append({"model_version": version, "model_name": model_name, "score_type": kind,
                                "split": split, "metric": k, "value": float(v) if v is not None else np.nan})

    for split in SPLITS:
        raw, cal = preds[split]
        record(champion_name, split, raw, "raw")
        record(champion_name, split, cal, "calibrated")
        for name in ("baseline_mean", "logistic_regression", "lightgbm"):
            if name != champion_name and split != "train":
                record(name, split, candidates[name].predict_proba(X[split])[:, 1], "raw")
        for kind, p in (("raw", raw), ("calibrated", cal)):
            ct = calibration_table(y[split], p, e[split], bins=mcfg["evaluation"]["calibration_bins"])
            ct.insert(0, "score_type", kind)
            ct.insert(0, "split", split)
            calib_rows.append(ct)
        cp = curve_points(y[split], cal)
        cp.insert(0, "split", split)
        curve_rows.append(cp)
        lt = lift_table(y[split], cal, e[split])
        lt.insert(0, "split", split)
        lift_rows.append(lt)

    metrics_df = pd.DataFrame(metric_rows)
    calib_df = pd.concat(calib_rows, ignore_index=True)
    curves_df = pd.concat(curve_rows, ignore_index=True)
    lift_df = pd.concat(lift_rows, ignore_index=True)
    for frame in (calib_df, curves_df, lift_df):
        frame.insert(0, "model_version", version)

    # ---------------- segment / fairness diagnostics on HOLDOUT
    ho = add_segments(parts["holdout"].copy(), settings)
    ho["predicted_pd"] = preds["holdout"][1]
    seg_frames = [group_performance(ho, g.lower(), "target", "predicted_pd", threshold, exposure_col,
                                    mcfg["evaluation"]["min_group_size"])
                  for g in mcfg["evaluation"]["fairness_groups"] if g.lower() in ho.columns]
    seg_df = pd.concat(seg_frames, ignore_index=True)
    seg_df.insert(0, "split", "holdout")
    seg_df.insert(0, "model_version", version)

    # ---------------- explainability
    from .explain import explain_model

    importance_df, lr_coef_df, explain_info = explain_model(pd_model, lr, parts, settings)

    # ---------------- persist artifact + reload check
    model_dir = settings.model_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    artifact = model_dir / f"{version}.joblib"
    joblib.dump(pd_model, artifact, compress=3)
    reloaded: PDModel = joblib.load(artifact)
    max_diff = float(np.max(np.abs(reloaded.predict_pd(parts["holdout"])[1] - preds["holdout"][1])))
    if max_diff > 1e-9:
        raise RuntimeError(f"reloaded model predictions differ (max abs diff {max_diff})")
    log.info("artifact %s written and reload-verified (max diff %.2e)", artifact.name, max_diff)

    ho_m = metrics_df.query("split=='holdout' and score_type=='calibrated' and model_name==@champion_name")
    ho_m = dict(zip(ho_m["metric"], ho_m["value"], strict=True))
    va_m = metrics_df.query("split=='validation' and score_type=='calibrated' and model_name==@champion_name")
    va_m = dict(zip(va_m["metric"], va_m["value"], strict=True))
    summary = {
        "model_version": version,
        "champion": champion_name,
        "selection_metric": primary,
        "validation_metrics_raw": {n: {k: round(v, 5) for k, v in m.items()} for n, m in val_metrics.items()},
        "champion_improvement_over_logreg": improvement,
        "calibration_comparison": cal_cmp.to_dict(orient="records"),
        "calibration_method": best_method,
        "threshold": threshold,
        "threshold_policy": tcfg,
        "best_iteration": best_iter,
        "n_features": len(features),
        "n_numeric": len(numeric),
        "n_categorical": len(categorical),
        "leakage_check": leakage_report,
        "fit_seconds": {k: round(v, 1) for k, v in timings.items()},
        "holdout_calibrated": ho_m,
        "validation_calibrated": va_m,
        "split_sizes": {s: int(len(parts[s])) for s in SPLITS},
        "explain": explain_info,
        "artifact": str(artifact.relative_to(PROJECT_ROOT)) if artifact.is_relative_to(PROJECT_ROOT) else str(artifact),
        "reload_max_abs_diff": max_diff,
    }

    with connect(settings) as conn, audited_run(conn, "train") as run_id:
        write_frame(conn, metrics_df, "analytics.model_metrics")
        write_frame(conn, calib_df, "analytics.calibration_table")
        write_frame(conn, curves_df, "analytics.model_curves")
        write_frame(conn, lift_df, "analytics.lift_table")
        write_frame(conn, seg_df, "analytics.segment_performance")
        write_frame(conn, importance_df, "analytics.feature_importance")
        write_frame(conn, lr_coef_df, "analytics.lr_coefficients")
        write_frame(conn, cal_cmp.assign(model_version=version), "analytics.calibration_comparison")
        conn.execute("UPDATE audit.model_registry SET is_champion = false")
        conn.execute(
            """INSERT INTO audit.model_registry
                 (model_version, algorithm, feature_version, feature_count, feature_schema_hash, calibration_method,
                  split_seed, data_fingerprint, selected_threshold, is_champion, artifact_path, params, metrics)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s)
               ON CONFLICT (model_version) DO UPDATE SET
                  trained_at = now(), calibration_method = EXCLUDED.calibration_method,
                  selected_threshold = EXCLUDED.selected_threshold, is_champion = true,
                  artifact_path = EXCLUDED.artifact_path, params = EXCLUDED.params, metrics = EXCLUDED.metrics,
                  data_fingerprint = EXCLUDED.data_fingerprint""",
            (version, champion_name, settings.feature_version, len(features), pd_model.feature_schema_hash,
             best_method, seed, _data_fingerprint(conn), threshold, summary["artifact"],
             json.dumps({"lightgbm": mcfg["models"]["lightgbm"] | {"best_iteration": best_iter},
                         "logistic_regression": mcfg["models"]["logistic_regression"]}),
             json.dumps({"holdout": ho_m, "validation": va_m}, default=float)),
        )
        q = QualityRunner(conn, run_id, "model")
        _model_checks(q, summary, parts, preds, mcfg)
        q.persist()
        q.raise_on_fail()

    write_json(settings.reports_dir / "model_metrics.json", summary)
    from .reporting import model_figures, write_model_card

    summary["figures"] = model_figures(settings, curves_df, calib_df, lift_df, metrics_df, champion_name)
    write_json(settings.reports_dir / "model_metrics.json", summary)
    write_model_card(settings, summary, metrics_df, seg_df, importance_df, lr_coef_df)
    return summary


def _data_fingerprint(conn) -> str:
    rows = conn.execute("""SELECT table_name, details->'fingerprint' FROM audit.table_load_log
                            WHERE load_id IN (SELECT max(load_id) FROM audit.table_load_log GROUP BY table_name)
                            ORDER BY table_name""").fetchall()
    import hashlib

    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()[:16]


def _model_checks(q: QualityRunner, summary: dict, parts: dict, preds: dict, mcfg: dict) -> None:
    ids = {s: set(parts[s]["sk_id_curr"]) for s in SPLITS}
    inter = len(ids["train"] & ids["validation"]) + len(ids["train"] & ids["holdout"]) + len(ids["validation"] & ids["holdout"])
    q.add("split_id_intersection", "analytics.model_split", "sk_id_curr", "pass" if inter == 0 else "fail", inter,
          "train/validation/holdout share no SK_ID_CURR")
    for s in SPLITS:
        p = preds[s][1]
        bad = int(np.sum((p < 0) | (p > 1) | np.isnan(p)))
        q.add("pd_range", f"model:{s}", "predicted_pd", "pass" if bad == 0 else "fail", bad, "0 <= PD <= 1")
    vm = summary["validation_metrics_raw"]
    champ = summary["champion"]
    better = vm[champ]["roc_auc"] > vm["baseline_mean"]["roc_auc"] + mcfg["selection"]["min_improvement_over_baseline"]
    q.add("champion_beats_baseline", "model", "roc_auc", "pass" if better else "fail",
          vm[champ]["roc_auc"] - vm["baseline_mean"]["roc_auc"], "champion AUC > baseline AUC + min improvement")
    drop = summary["validation_calibrated"]["roc_auc"] - summary["holdout_calibrated"]["roc_auc"]
    q.add("holdout_auc_drop", "model", "roc_auc", "warn" if drop > mcfg["evaluation"]["max_auc_drop_alert"] else "pass",
          drop, f"validation AUC - holdout AUC <= {mcfg['evaluation']['max_auc_drop_alert']}")
    q.add("artifact_reload", "model", None, "pass" if summary["reload_max_abs_diff"] <= 1e-9 else "fail",
          summary["reload_max_abs_diff"], "reloaded artifact reproduces predictions")


def load_champion(settings: Settings) -> PDModel:
    with connect(settings) as conn:
        row = conn.execute("SELECT artifact_path FROM audit.model_registry WHERE is_champion ORDER BY trained_at DESC "
                           "LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("no champion model registered; run `credit-risk train` first")
    path = Path(row[0])
    path = path if path.is_absolute() else PROJECT_ROOT / path
    return joblib.load(path)


__all__ = ["train_and_evaluate", "load_champion", "build_lr_pipeline", "fit_lgbm", "QuantileClipper", "clone"]
