"""Model evaluation metrics (spec §12.5). Pure functions over numpy arrays so they are unit-testable."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)

EPS = 1e-15


def ks_statistic(y: np.ndarray, p: np.ndarray) -> float:
    """Kolmogorov–Smirnov: max distance between score CDFs of positives and negatives (= max TPR-FPR)."""
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def wilson_interval(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    phat = k / n
    denom = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denom
    half = z * np.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return (float(max(0.0, centre - half)), float(min(1.0, centre + half)))


def threshold_metrics(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, float]:
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"threshold": float(threshold), "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
            "precision": float(precision), "recall": float(recall), "f1": float(f1),
            "flagged_share": float(pred.mean())}


def core_metrics(y: np.ndarray, p: np.ndarray, exposure: np.ndarray | None = None,
                 threshold: float | None = None) -> dict[str, float]:
    y = np.asarray(y).astype(int)
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    out = {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "observed_rate": float(y.mean()),
        "mean_pd": float(p.mean()),
        "calibration_in_the_large": float(p.mean() - y.mean()),
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else np.nan,
        "pr_auc": float(average_precision_score(y, p)) if len(np.unique(y)) > 1 else np.nan,
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "ks": ks_statistic(y, p) if len(np.unique(y)) > 1 else np.nan,
    }
    lift = lift_table(y, p, exposure)
    out["top_decile_lift"] = float(lift.iloc[0]["lift"])
    out["top_decile_capture"] = float(lift.iloc[0]["cum_capture_rate"])
    if exposure is not None:
        out["top_decile_bad_exposure_capture"] = float(lift.iloc[0]["cum_bad_exposure_capture"])
    if threshold is not None:
        tm = threshold_metrics(y, p, threshold)
        out.update({f"{k}_at_threshold": v for k, v in tm.items() if k != "threshold"})
        out["threshold"] = float(threshold)
        if exposure is not None:
            e = np.asarray(exposure, dtype=float)
            flagged = p >= threshold
            bad_e = e[y == 1].sum()
            out["bad_exposure_capture_at_threshold"] = float(e[flagged & (y == 1)].sum() / bad_e) if bad_e else np.nan
            out["expected_loss_capture_at_threshold"] = float((p * e)[flagged].sum() / (p * e).sum())
    return out


def lift_table(y: np.ndarray, p: np.ndarray, exposure: np.ndarray | None = None, bins: int = 10) -> pd.DataFrame:
    """Deciles by descending PD: lift, cumulative capture (customers and exposure-weighted)."""
    df = pd.DataFrame({"y": np.asarray(y).astype(int), "p": np.asarray(p, dtype=float)})
    df["e"] = 1.0 if exposure is None else np.asarray(exposure, dtype=float)
    df = df.sort_values("p", ascending=False, kind="mergesort").reset_index(drop=True)
    df["decile"] = (np.arange(len(df)) * bins // len(df)) + 1
    g = df.groupby("decile").agg(customers=("y", "size"), positives=("y", "sum"), mean_pd=("p", "mean"),
                                 min_pd=("p", "min"), max_pd=("p", "max"), exposure=("e", "sum"))
    g["bad_exposure"] = df[df["y"] == 1].groupby("decile")["e"].sum().reindex(g.index, fill_value=0.0)
    total_pos = max(df["y"].sum(), 1)
    base_rate = df["y"].mean() if len(df) else np.nan
    g["observed_rate"] = g["positives"] / g["customers"]
    g["lift"] = g["observed_rate"] / base_rate if base_rate else np.nan
    g["cum_capture_rate"] = g["positives"].cumsum() / total_pos
    g["cum_customer_share"] = g["customers"].cumsum() / len(df)
    g["cum_bad_exposure_capture"] = g["bad_exposure"].cumsum() / max(g["bad_exposure"].sum(), 1e-12)
    g["exposure_share"] = g["exposure"] / g["exposure"].sum()
    return g.reset_index()


def calibration_table(y: np.ndarray, p: np.ndarray, exposure: np.ndarray | None = None, bins: int = 10,
                      min_bin_size: int = 100) -> pd.DataFrame:
    """Equal-count bins of predicted PD with observed rate and Wilson 95% CI."""
    df = pd.DataFrame({"y": np.asarray(y).astype(int), "p": np.asarray(p, dtype=float)})
    df["e"] = 0.0 if exposure is None else np.asarray(exposure, dtype=float)
    df["bin"] = pd.qcut(df["p"].rank(method="first"), q=min(bins, len(df)), labels=False) + 1
    g = df.groupby("bin").agg(customers=("y", "size"), positives=("y", "sum"), mean_pd=("p", "mean"),
                              pd_lower=("p", "min"), pd_upper=("p", "max"), exposure=("e", "sum")).reset_index()
    g["observed_rate"] = g["positives"] / g["customers"]
    ci = g.apply(lambda r: wilson_interval(r["positives"], r["customers"]), axis=1)
    g["ci_low"] = [c[0] for c in ci]
    g["ci_high"] = [c[1] for c in ci]
    g["small_sample"] = g["customers"] < min_bin_size
    return g


def curve_points(y: np.ndarray, p: np.ndarray, n_points: int = 201) -> pd.DataFrame:
    """ROC and PR curves resampled onto a fixed grid (compact enough for Power BI)."""
    fpr, tpr, roc_thr = roc_curve(y, p)
    grid = np.linspace(0, 1, n_points)
    roc = pd.DataFrame({"curve": "roc", "x": grid, "y": np.interp(grid, fpr, tpr)})
    roc["threshold"] = np.interp(grid, fpr, np.clip(roc_thr, 0, 1))
    prec, rec, pr_thr = precision_recall_curve(y, p)
    order = np.argsort(rec)
    pr = pd.DataFrame({"curve": "pr", "x": grid, "y": np.interp(grid, rec[order], prec[order])})
    pr["threshold"] = np.interp(grid, rec[:-1][::-1], pr_thr[::-1]) if len(pr_thr) else np.nan
    return pd.concat([roc, pr], ignore_index=True)


def group_performance(df: pd.DataFrame, group_col: str, y_col: str, p_col: str, threshold: float,
                      exposure_col: str | None = None, min_group_size: int = 500) -> pd.DataFrame:
    """Per-group diagnostics for fairness/bias review (descriptive, not a proof of discrimination)."""
    rows = []
    for value, g in df.groupby(group_col, dropna=False):
        y, p = g[y_col].to_numpy().astype(int), g[p_col].to_numpy()
        n = len(g)
        auc = roc_auc_score(y, p) if (len(np.unique(y)) > 1 and n >= 50) else np.nan
        tm = threshold_metrics(y, p, threshold)
        rows.append({
            "group_attribute": group_col,
            "group_value": "Missing" if pd.isna(value) else str(value),
            "customers": n,
            "positives": int(y.sum()),
            "observed_rate": float(y.mean()),
            "mean_pd": float(p.mean()),
            "roc_auc": float(auc) if not np.isnan(auc) else np.nan,
            "recall_at_threshold": tm["recall"],
            "precision_at_threshold": tm["precision"],
            "flagged_share": tm["flagged_share"],
            "exposure": float(g[exposure_col].sum()) if exposure_col else np.nan,
            "small_sample_warning": n < min_group_size,
        })
    return pd.DataFrame(rows)
