"""Unit tests for credit_risk.stats (no database required)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from credit_risk import stats  # noqa: E402


def test_wilson_ci_known_values():
    # Reference values (Wilson 1927; also statsmodels proportion_confint(method="wilson"))
    low, high = stats.wilson_ci(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-4)
    assert high == pytest.approx(0.7634, abs=1e-4)

    low, high = stats.wilson_ci(0, 20)
    assert low == pytest.approx(0.0, abs=1e-12)
    assert high == pytest.approx(0.1611, abs=1e-4)


def test_wilson_ci_matches_statsmodels():
    from statsmodels.stats.proportion import proportion_confint

    for k, n in [(1, 7), (27, 300), (2732, 30000)]:
        ref = proportion_confint(k, n, alpha=0.05, method="wilson")
        assert stats.wilson_ci(k, n) == pytest.approx(ref, abs=1e-9)


def test_wilson_ci_empty_group_is_nan():
    low, high = stats.wilson_ci(0, 0)
    assert np.isnan(low) and np.isnan(high)


def test_cramers_v_perfect_association_is_one():
    assert stats.cramers_v([[10, 0], [0, 10]]) == pytest.approx(1.0)
    assert stats.cramers_v([[30, 0, 0], [0, 25, 0], [0, 0, 40]]) == pytest.approx(1.0)


def test_cramers_v_independence_is_zero():
    assert stats.cramers_v([[20, 20], [40, 40]]) == pytest.approx(0.0, abs=1e-12)


def test_chi_square_uses_fisher_for_sparse_2x2():
    res = stats.chi_square([[3, 1], [1, 3]])
    assert res["test"] == "Fisher exact"
    assert 0 <= res["p_value"] <= 1
    big = stats.chi_square([[300, 100], [100, 300]])
    assert big["test"] == "Chi-square"


def test_bh_monotone_and_bounded():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 50) ** 3
    adj = stats.benjamini_hochberg(p)
    order = np.argsort(p)
    assert np.all(np.diff(adj[order]) >= -1e-12)  # monotone in the raw p ordering
    assert np.all(adj >= p - 1e-12)
    assert np.all(adj <= 1.0)
    assert np.all(stats.bonferroni(p) >= adj - 1e-12)  # Bonferroni is more conservative


def test_bh_matches_statsmodels_and_handles_nan():
    from statsmodels.stats.multitest import multipletests

    p = np.array([0.01, 0.04, 0.03, 0.2, 0.001])
    ref = multipletests(p, method="fdr_bh")[1]
    np.testing.assert_allclose(stats.benjamini_hochberg(p), ref)
    with_nan = stats.benjamini_hochberg(np.append(p, np.nan))
    assert np.isnan(with_nan[-1])
    np.testing.assert_allclose(with_nan[:-1], ref)


def test_rank_biserial_sign():
    rng = np.random.default_rng(1)
    low = rng.normal(0, 1, 500)
    high = rng.normal(1, 1, 500)
    assert stats.mann_whitney(high, low)["effect_size"] > 0
    assert stats.mann_whitney(low, high)["effect_size"] < 0
    # complete separation -> |r| = 1
    assert stats.mann_whitney([10, 11, 12], [1, 2, 3])["effect_size"] == pytest.approx(1.0)
    assert stats.mann_whitney([1, 2, 3], [10, 11, 12])["effect_size"] == pytest.approx(-1.0)


def test_welch_t_direction_and_d():
    res = stats.welch_t([2.0, 3.0, 4.0, 5.0], [0.0, 1.0, 2.0, 3.0])
    assert res["statistic"] > 0
    assert res["effect_size"] > 0


def test_rate_table_ci_contains_rate():
    groups = pd.Series(["a"] * 50 + ["b"] * 50 + [None] * 10)
    target = pd.Series([1] * 5 + [0] * 45 + [1] * 20 + [0] * 30 + [0] * 10)
    exposure = pd.Series([1.0] * 110)
    out = stats.rate_table(groups, target, exposure)
    assert set(out["group"]) == {"a", "b", "(missing)"}
    assert np.all((out["ci_low"] <= out["rate"]) & (out["rate"] <= out["ci_high"]))
    assert out["customer_share"].sum() == pytest.approx(1.0)
    assert out["exposure_share"].sum() == pytest.approx(1.0)


def test_adjust_and_interpret():
    df = pd.DataFrame({"p_value": [1e-10, 0.5], "effect_label": ["negligible", "medium"]})
    out = stats.adjust_results(df)
    assert "p_adj_bh" in out and "p_adj_bonferroni" in out
    assert "대표본" in stats.interpret(out.iloc[0])
    assert "유의하지 않음" in stats.interpret(out.iloc[1])
