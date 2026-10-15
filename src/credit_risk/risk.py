"""PD / Exposure / LGD / Expected Loss rules (spec §13). Business rules live here and in SQL
(sql/04_mart) — both read the same configs/project.yaml values; DAX only aggregates."""

from __future__ import annotations

import numpy as np
import pandas as pd


def clip_pd(pd_values: np.ndarray, low: float, high: float) -> np.ndarray:
    return np.clip(np.asarray(pd_values, dtype=float), low, high)


def assign_grade(pd_values: np.ndarray, grades: list[dict]) -> np.ndarray:
    """Map PD to grade using [lower, upper) intervals from config. Raises if a PD falls outside all grades."""
    p = np.asarray(pd_values, dtype=float)
    out = np.full(p.shape, None, dtype=object)
    for g in grades:
        mask = (p >= g["lower"]) & (p < g["upper"])
        out[mask] = g["grade"]
    if np.any(pd.isna(out)) and np.any(~np.isnan(p[pd.isna(out)])):
        raise ValueError("some PD values are not covered by the configured risk grades")
    return out


def expected_loss(pd_values: np.ndarray, exposure: np.ndarray, lgd: float | np.ndarray) -> np.ndarray:
    """EL_i = PD_i x Exposure_i x LGD_i (proxy: AMT_CREDIT exposure, assumed LGD)."""
    p = np.asarray(pd_values, dtype=float)
    e = np.asarray(exposure, dtype=float)
    if np.any(p < 0) or np.any(p > 1):
        raise ValueError("PD must be within [0, 1]")
    if np.any(e < 0):
        raise ValueError("exposure must be non-negative")
    return p * e * lgd


def scenario_expected_loss(pd_values: np.ndarray, exposure: np.ndarray, lgd: float, pd_stress: float = 0.0,
                           exposure_change: float = 0.0) -> float:
    """Same rule as the DAX `Scenario Expected Loss`: relative multipliers, stressed PD capped at 1."""
    p = np.minimum(1.0, np.asarray(pd_values, dtype=float) * (1 + pd_stress))
    return float(np.sum(p * np.asarray(exposure, dtype=float) * (1 + exposure_change) * lgd))
