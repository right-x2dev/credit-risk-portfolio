"""Explainability (spec §12.7): global importance, SHAP (LightGBM native TreeSHAP via pred_contrib),
logistic-regression odds ratios, and per-customer top reason codes.

SHAP values describe how the *model* uses a feature; they are associations, not causal effects.
"""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .calibrate import PDModel  # noqa: E402
from .config import Settings  # noqa: E402

log = logging.getLogger(__name__)
NAVY, AMBER, RED, GREY = "#1F3A5F", "#E0A030", "#B8423A", "#8A94A6"


def tree_contributions(model: PDModel, X: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """TreeSHAP contributions in log-odds space: shape (n, n_features); last column (bias) dropped."""
    pipe = model.pipeline
    Xt = pipe.named_steps["pre"].transform(X[model.features])
    contrib = pipe.named_steps["clf"].booster_.predict(Xt, pred_contrib=True)
    return contrib[:, :-1], model.features


def linear_contributions(model: PDModel, X: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Coefficient x standardised value, summed back to the original feature (one-hot groups summed)."""
    pipe = model.pipeline
    pre, clf = pipe.named_steps["pre"], pipe.named_steps["clf"]
    Xt = pre.transform(X[model.features])
    names = pre.get_feature_names_out()
    contrib = Xt * clf.coef_[0]
    owner = []
    for n in names:
        base = n.split("__", 1)[1]
        match = next((f for f in model.features if base == f or base.startswith(f + "_") or base == f"missingindicator_{f}"),
                     base)
        owner.append(match)
    frame = pd.DataFrame(contrib, columns=owner)
    grouped = frame.T.groupby(level=0).sum().T
    grouped = grouped.reindex(columns=model.features, fill_value=0.0)
    return grouped.to_numpy(), model.features


def contributions(model: PDModel, X: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    if model.algorithm == "lightgbm":
        return tree_contributions(model, X)
    return linear_contributions(model, X)


def reason_codes(model: PDModel, X: pd.DataFrame, ids: np.ndarray, top_k: int = 3,
                 chunk: int = 50_000) -> pd.DataFrame:
    """Top-k risk-increasing features per customer (positive contribution to log-odds)."""
    frames = []
    for start in range(0, len(X), chunk):
        part = X.iloc[start:start + chunk]
        contrib, names = contributions(model, part)
        order = np.argsort(-contrib, axis=1)[:, :top_k]
        rows = np.arange(len(part))[:, None]
        vals = contrib[rows, order]
        names_arr = np.asarray(names, dtype=object)[order]
        values = part[list(names)].to_numpy(dtype=object)
        for k in range(top_k):
            feat = names_arr[:, k]
            raw_vals = values[np.arange(len(part)), order[:, k]]
            frames.append(pd.DataFrame({
                "sk_id_curr": ids[start:start + chunk],
                "reason_rank": k + 1,
                "feature": np.where(vals[:, k] > 0, feat, None),
                "contribution_logodds": np.where(vals[:, k] > 0, vals[:, k], np.nan),
                "feature_value": [None if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v)
                                  for v in raw_vals],
            }))
    out = pd.concat(frames, ignore_index=True)
    out.loc[out["feature"].isna(), "feature_value"] = None
    return out.sort_values(["sk_id_curr", "reason_rank"]).reset_index(drop=True)


def explain_model(model: PDModel, lr_pipeline, parts: dict[str, pd.DataFrame],
                  settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    ecfg = settings.model["explain"]
    fig_dir = settings.reports_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    ho = parts["holdout"]
    sample = ho.sample(n=min(ecfg["shap_sample_size"], len(ho)), random_state=settings.random_seed)
    contrib, names = contributions(model, sample)
    mean_abs = np.abs(contrib).mean(axis=0)
    imp = pd.DataFrame({"feature": names, "mean_abs_shap": mean_abs})
    if model.algorithm == "lightgbm":
        booster = model.pipeline.named_steps["clf"].booster_
        imp["gain_importance"] = booster.feature_importance(importance_type="gain")
        imp["split_importance"] = booster.feature_importance(importance_type="split")
    else:
        imp["gain_importance"] = np.nan
        imp["split_importance"] = np.nan
    imp = imp.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)
    imp["rank"] = np.arange(1, len(imp) + 1)
    imp["model_version"] = model.model_version
    # direction: correlation between feature value and its SHAP value (sign only, descriptive)
    directions = []
    for f in imp["feature"]:
        j = names.index(f)
        v = pd.to_numeric(sample[f], errors="coerce").to_numpy(dtype=float)
        ok = ~np.isnan(v)
        if ok.sum() > 30 and np.std(v[ok]) > 0 and np.std(contrib[ok, j]) > 0:
            r = np.corrcoef(v[ok], contrib[ok, j])[0, 1]
            directions.append("higher value -> higher PD" if r > 0.1 else "higher value -> lower PD" if r < -0.1
                              else "non-monotonic / mixed")
        else:
            directions.append("categorical / mixed")
    imp["direction"] = directions

    # logistic regression coefficients (standardised inputs -> comparable magnitudes)
    pre, clf = lr_pipeline.named_steps["pre"], lr_pipeline.named_steps["clf"]
    coef = pd.DataFrame({"term": pre.get_feature_names_out(), "coefficient": clf.coef_[0]})
    coef["odds_ratio_per_sd"] = np.exp(coef["coefficient"])
    coef["abs_coefficient"] = coef["coefficient"].abs()
    coef = coef.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)
    coef["rank"] = np.arange(1, len(coef) + 1)
    coef["model_version"] = model.model_version

    top = imp.head(ecfg["top_n_features"])
    paths = {}
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(top["feature"][::-1], top["mean_abs_shap"][::-1], color=NAVY)
    ax.set_xlabel("mean |SHAP| (log-odds)")
    ax.set_title(f"Global feature importance — top {len(top)} (holdout sample, n={len(sample):,})")
    fig.tight_layout()
    paths["shap_importance"] = str(fig_dir / "model_shap_importance.png")
    fig.savefig(paths["shap_importance"], dpi=130)
    plt.close(fig)

    # beeswarm-style summary
    fig, ax = plt.subplots(figsize=(9, 6.5))
    rng = np.random.default_rng(settings.random_seed)
    for i, f in enumerate(top["feature"][::-1]):
        j = names.index(f)
        v = pd.to_numeric(sample[f], errors="coerce").to_numpy(dtype=float)
        col = np.full(len(v), 0.5)
        ok = ~np.isnan(v)
        if ok.sum() > 1 and np.nanstd(v) > 0:
            lo, hi = np.nanpercentile(v, [5, 95])
            col[ok] = np.clip((v[ok] - lo) / (hi - lo + 1e-12), 0, 1)
        idx = rng.choice(len(v), size=min(2000, len(v)), replace=False)
        ax.scatter(contrib[idx, j], i + rng.normal(0, 0.08, len(idx)), c=col[idx], cmap="coolwarm", s=4, alpha=0.6)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(top["feature"][::-1])
    ax.axvline(0, color=GREY, lw=0.8)
    ax.set_xlabel("SHAP value (impact on log-odds of PD)   colour: feature value low (blue) -> high (red)")
    ax.set_title("SHAP summary (association, not causation)")
    fig.tight_layout()
    paths["shap_summary"] = str(fig_dir / "model_shap_summary.png")
    fig.savefig(paths["shap_summary"], dpi=130)
    plt.close(fig)

    for f in top["feature"].head(3):
        j = names.index(f)
        v = pd.to_numeric(sample[f], errors="coerce").to_numpy(dtype=float)
        if np.isnan(v).all():
            continue
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.scatter(v, contrib[:, j], s=4, alpha=0.4, color=NAVY)
        ax.axhline(0, color=GREY, lw=0.8)
        ax.set_xlabel(f)
        ax.set_ylabel("SHAP value (log-odds)")
        ax.set_title(f"SHAP dependence — {f}")
        fig.tight_layout()
        key = f"shap_dependence_{f}"
        paths[key] = str(fig_dir / f"model_shap_dependence_{f}.png")
        fig.savefig(paths[key], dpi=130)
        plt.close(fig)
    info = {"shap_sample_size": int(len(sample)), "figures": paths,
            "top_features": top[["feature", "mean_abs_shap", "direction"]].to_dict(orient="records")}
    return imp, coef, info
