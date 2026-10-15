"""Probability calibration and the serialisable PD model wrapper (spec §12.6, §12.9).

Calibrators are fitted on the VALIDATION split only — never on holdout. The method (Platt/sigmoid vs
isotonic) is chosen by K-fold cross-validation *inside* the validation split so the comparison does not
reuse the very rows it is scored on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import StratifiedKFold

EPS = 1e-6


class FeatureSchemaError(ValueError):
    """Scoring data does not match the feature schema the model was trained on."""


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


class SigmoidCalibrator:
    """Platt scaling on the logit of the raw score."""

    method = "sigmoid"

    def fit(self, p_raw: np.ndarray, y: np.ndarray) -> SigmoidCalibrator:
        self.lr_ = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p_raw).reshape(-1, 1), y)
        return self

    def transform(self, p_raw: np.ndarray) -> np.ndarray:
        return self.lr_.predict_proba(_logit(p_raw).reshape(-1, 1))[:, 1]


class IsotonicCalibrator:
    method = "isotonic"

    def fit(self, p_raw: np.ndarray, y: np.ndarray) -> IsotonicCalibrator:
        self.iso_ = IsotonicRegression(out_of_bounds="clip", y_min=EPS, y_max=1 - EPS).fit(p_raw, y)
        return self

    def transform(self, p_raw: np.ndarray) -> np.ndarray:
        return self.iso_.predict(p_raw)


CALIBRATORS = {"sigmoid": SigmoidCalibrator, "isotonic": IsotonicCalibrator}


def compare_calibrators(p_raw: np.ndarray, y: np.ndarray, methods: list[str], folds: int, seed: int) -> pd.DataFrame:
    """Cross-validated Brier / log loss of each calibration method within the validation split."""
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    rows = []
    for method in ["none", *methods]:
        oof = np.zeros_like(p_raw, dtype=float)
        for tr, te in skf.split(p_raw.reshape(-1, 1), y):
            oof[te] = p_raw[te] if method == "none" else CALIBRATORS[method]().fit(p_raw[tr], y[tr]).transform(p_raw[te])
        rows.append({"method": method, "cv_brier": brier_score_loss(y, oof),
                     "cv_log_loss": log_loss(y, np.clip(oof, EPS, 1 - EPS)), "cv_mean_pd": float(oof.mean()),
                     "observed_rate": float(y.mean())})
    return pd.DataFrame(rows)


def schema_hash(numeric: list[str], categorical: list[str]) -> str:
    payload = "num:" + ",".join(numeric) + "|cat:" + ",".join(categorical)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class PDModel:
    """Preprocessing+model pipeline + calibrator, serialised as one joblib artifact."""

    model_version: str
    algorithm: str
    pipeline: Any
    calibrator: Any | None
    numeric_features: list[str]
    categorical_features: list[str]
    pd_clip: tuple[float, float] = (1e-4, 1 - 1e-4)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def features(self) -> list[str]:
        return self.numeric_features + self.categorical_features

    @property
    def feature_schema_hash(self) -> str:
        return schema_hash(self.numeric_features, self.categorical_features)

    def _check_schema(self, X: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.features if c not in X.columns]
        if missing:
            raise FeatureSchemaError(f"{len(missing)} model feature(s) missing from scoring data: {missing[:10]}")
        return X[self.features]

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        X = self._check_schema(X)
        return self.pipeline.predict_proba(X)[:, 1]

    def predict_pd(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return (predicted_pd_raw, predicted_pd calibrated + clipped)."""
        raw = self.predict_raw(X)
        cal = raw if self.calibrator is None else self.calibrator.transform(raw)
        return raw, np.clip(cal, *self.pd_clip)
