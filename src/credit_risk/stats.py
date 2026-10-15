"""Statistical tests used by the EDA (spec §11.2).

Design rules:
- Every test returns an effect size next to the p-value. With ~300k rows almost any difference is
  "significant", so conclusions are driven by effect sizes, never by p-values alone.
- Functions are pure (numpy/pandas in, dict/DataFrame out) so they can be unit-tested without a DB.
- Multiple comparisons are corrected with Benjamini–Hochberg (FDR); Bonferroni is reported as the
  conservative reference.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sps

# Conventional (Cohen-style) thresholds used only to attach a verbal label to an effect size.
RANK_BISERIAL_LABELS = ((0.1, "negligible"), (0.3, "small"), (0.5, "medium"), (math.inf, "large"))
CRAMERS_V_LABELS = ((0.1, "negligible"), (0.3, "small"), (0.5, "medium"), (math.inf, "large"))
COHENS_D_LABELS = ((0.2, "negligible"), (0.5, "small"), (0.8, "medium"), (math.inf, "large"))

EFFECT_LABEL_KO = {"negligible": "무시할 수준", "small": "작음", "medium": "중간", "large": "큼"}


def effect_label(value: float, thresholds: Sequence[tuple[float, str]] = RANK_BISERIAL_LABELS) -> str:
    """Verbal label for |effect size| (upper-exclusive thresholds)."""
    if value is None or not np.isfinite(value):
        return "n/a"
    v = abs(float(value))
    for upper, label in thresholds:
        if v < upper:
            return label
    return thresholds[-1][1]


# --------------------------------------------------------------------------------------------
# Proportions
# --------------------------------------------------------------------------------------------
def wilson_ci(successes: float, n: float, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Returns (nan, nan) for n == 0."""
    if n <= 0:
        return (float("nan"), float("nan"))
    z = float(sps.norm.ppf(1 - alpha / 2))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate_table(
    groups: pd.Series, target: pd.Series, exposure: pd.Series | None = None, alpha: float = 0.05
) -> pd.DataFrame:
    """Observed difficulty rate per group with Wilson CI (and exposure share if given).

    `groups` may contain NaN; those rows are reported as the group "(missing)".
    """
    frame = pd.DataFrame({"group": groups, "target": target})
    if exposure is not None:
        frame["exposure"] = exposure
    if isinstance(frame["group"].dtype, pd.CategoricalDtype):
        frame["group"] = frame["group"].cat.add_categories(["(missing)"]).fillna("(missing)")
    else:
        frame["group"] = frame["group"].astype(object).where(frame["group"].notna(), "(missing)")
    agg: dict[str, Any] = {"n": ("target", "size"), "events": ("target", "sum")}
    if exposure is not None:
        agg["exposure"] = ("exposure", "sum")
    out = frame.groupby("group", observed=True, sort=True).agg(**agg).reset_index()
    out = out[out["n"] > 0].reset_index(drop=True)
    out["rate"] = out["events"] / out["n"]
    cis = [wilson_ci(e, n, alpha) for e, n in zip(out["events"], out["n"], strict=True)]
    out["ci_low"] = [c[0] for c in cis]
    out["ci_high"] = [c[1] for c in cis]
    out["customer_share"] = out["n"] / out["n"].sum()
    if exposure is not None:
        out["exposure_share"] = out["exposure"] / out["exposure"].sum()
        out["exposure_to_customer_ratio"] = out["exposure_share"] / out["customer_share"]
    return out


# --------------------------------------------------------------------------------------------
# Numeric: Mann–Whitney U + rank-biserial, Welch t + Cohen's d
# --------------------------------------------------------------------------------------------
def _clean(x: Any) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    return arr[np.isfinite(arr)]


def rank_biserial(u1: float, n1: int, n0: int) -> float:
    """Rank-biserial correlation from U of group 1: r = 2*U1/(n1*n0) - 1.

    r > 0 means values in group 1 tend to be larger than in group 0 (r = P(X1>X0) - P(X1<X0)).
    """
    if n1 == 0 or n0 == 0:
        return float("nan")
    return 2.0 * u1 / (n1 * n0) - 1.0


def mann_whitney(group1: Any, group0: Any) -> dict[str, Any]:
    """Two-sided Mann–Whitney U test of group1 (e.g. TARGET=1) vs group0, with rank-biserial r."""
    x1, x0 = _clean(group1), _clean(group0)
    n1, n0 = len(x1), len(x0)
    if n1 == 0 or n0 == 0:
        return {"test": "Mann-Whitney U", "n1": n1, "n0": n0, "statistic": np.nan, "p_value": np.nan,
                "effect_size": np.nan, "effect_name": "rank-biserial r", "effect_label": "n/a"}
    res = sps.mannwhitneyu(x1, x0, alternative="two-sided", method="asymptotic")
    r = rank_biserial(float(res.statistic), n1, n0)
    return {
        "test": "Mann-Whitney U",
        "n1": n1,
        "n0": n0,
        "statistic": float(res.statistic),
        "p_value": float(res.pvalue),
        "effect_size": r,
        "effect_name": "rank-biserial r",
        "effect_label": effect_label(r, RANK_BISERIAL_LABELS),
        "median1": float(np.median(x1)),
        "median0": float(np.median(x0)),
    }


def cohens_d(group1: Any, group0: Any) -> float:
    x1, x0 = _clean(group1), _clean(group0)
    n1, n0 = len(x1), len(x0)
    if n1 < 2 or n0 < 2:
        return float("nan")
    pooled = math.sqrt(((n1 - 1) * x1.var(ddof=1) + (n0 - 1) * x0.var(ddof=1)) / (n1 + n0 - 2))
    return float((x1.mean() - x0.mean()) / pooled) if pooled > 0 else float("nan")


def welch_t(group1: Any, group0: Any) -> dict[str, Any]:
    """Welch's t-test (unequal variances) with Cohen's d. Appropriate when both groups are large
    (CLT) and the mean is a meaningful summary; for heavy-tailed amounts prefer Mann–Whitney."""
    x1, x0 = _clean(group1), _clean(group0)
    if len(x1) < 2 or len(x0) < 2:
        return {"test": "Welch t", "statistic": np.nan, "p_value": np.nan, "effect_size": np.nan,
                "effect_name": "Cohen's d", "effect_label": "n/a"}
    res = sps.ttest_ind(x1, x0, equal_var=False)
    d = cohens_d(x1, x0)
    return {
        "test": "Welch t",
        "n1": len(x1),
        "n0": len(x0),
        "statistic": float(res.statistic),
        "p_value": float(res.pvalue),
        "effect_size": d,
        "effect_name": "Cohen's d",
        "effect_label": effect_label(d, COHENS_D_LABELS),
        "mean1": float(x1.mean()),
        "mean0": float(x0.mean()),
    }


# --------------------------------------------------------------------------------------------
# Categorical: Chi-square + Cramér's V, Fisher exact for sparse 2x2
# --------------------------------------------------------------------------------------------
def cramers_v(table: Any) -> float:
    """Cramér's V (no bias correction, no Yates) for an r x c contingency table."""
    t = np.asarray(table, dtype=float)
    t = t[t.sum(axis=1) > 0][:, t.sum(axis=0) > 0]
    n = t.sum()
    k = min(t.shape) - 1
    if n == 0 or k < 1:
        return float("nan")
    chi2 = sps.chi2_contingency(t, correction=False)[0]
    return float(math.sqrt(chi2 / (n * k)))


def chi_square(table: Any, min_expected: float = 5.0) -> dict[str, Any]:
    """Chi-square test of independence with Cramér's V.

    For a 2x2 table with any expected count < `min_expected`, Fisher's exact test is used instead
    (the effect size is still Cramér's V = |phi|).
    """
    t = np.asarray(table, dtype=float)
    t = t[t.sum(axis=1) > 0][:, t.sum(axis=0) > 0]
    if min(t.shape) < 2:
        return {"test": "Chi-square", "statistic": np.nan, "p_value": np.nan, "dof": 0,
                "effect_size": np.nan, "effect_name": "Cramér's V", "effect_label": "n/a"}
    chi2, p, dof, expected = sps.chi2_contingency(t, correction=False)
    v = cramers_v(t)
    result = {
        "test": "Chi-square",
        "statistic": float(chi2),
        "p_value": float(p),
        "dof": int(dof),
        "effect_size": v,
        "effect_name": "Cramér's V",
        "effect_label": effect_label(v, CRAMERS_V_LABELS),
        "min_expected": float(expected.min()),
    }
    if t.shape == (2, 2) and expected.min() < min_expected:
        odds, p_f = sps.fisher_exact(t.astype(int))
        result.update({"test": "Fisher exact", "statistic": float(odds), "p_value": float(p_f)})
    return result


def crosstab_test(groups: pd.Series, target: pd.Series) -> dict[str, Any]:
    """Chi-square / Fisher test of a categorical series against a binary target (NaN as own level)."""
    g = groups.astype(object).where(groups.notna(), "(missing)")
    table = pd.crosstab(g, target)
    out = chi_square(table.to_numpy())
    out["levels"] = int(table.shape[0])
    return out


# --------------------------------------------------------------------------------------------
# Multiple comparisons
# --------------------------------------------------------------------------------------------
def benjamini_hochberg(p_values: Sequence[float]) -> np.ndarray:
    """Benjamini–Hochberg FDR-adjusted p-values (NaN-safe: NaN stays NaN and is not counted)."""
    p = np.asarray(p_values, dtype=float)
    out = np.full_like(p, np.nan)
    mask = np.isfinite(p)
    m = int(mask.sum())
    if m == 0:
        return out
    pv = p[mask]
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    adj = np.clip(adj, 0, 1)
    res = np.empty(m)
    res[order] = adj
    out[mask] = res
    return out


def bonferroni(p_values: Sequence[float]) -> np.ndarray:
    p = np.asarray(p_values, dtype=float)
    m = int(np.isfinite(p).sum())
    return np.clip(p * max(m, 1), 0, 1)


def adjust_results(results: pd.DataFrame, p_col: str = "p_value") -> pd.DataFrame:
    """Add BH and Bonferroni adjusted p-values to a results table (over the whole family)."""
    out = results.copy()
    out["p_adj_bh"] = benjamini_hochberg(out[p_col].to_numpy())
    out["p_adj_bonferroni"] = bonferroni(out[p_col].to_numpy())
    return out


def interpret(row: pd.Series | dict, alpha: float = 0.05) -> str:
    """Korean interpretation driven by effect size; significance is mentioned only as a qualifier."""
    label = row.get("effect_label", "n/a")
    p_adj = row.get("p_adj_bh", row.get("p_value"))
    sig = p_adj is not None and np.isfinite(p_adj) and p_adj < alpha
    label_ko = EFFECT_LABEL_KO.get(label, "산출 불가")
    if label == "n/a":
        return "검정 불가(표본 부족)"
    if not sig:
        return f"효과크기 {label_ko}, BH 보정 후 유의하지 않음"
    if label == "negligible":
        return "통계적으로 유의하나 효과크기는 무시할 수준 (대표본 효과)"
    return f"효과크기 {label_ko} — 실무적으로 검토할 만한 연관"
