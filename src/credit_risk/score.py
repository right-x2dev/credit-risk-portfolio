"""Scoring (spec Phase 6): champion model -> analytics.model_predictions + analytics.reason_codes.

Every customer in the feature mart is scored and tagged with its dataset role:
  train / validation / holdout  -> labelled application_train customers (only holdout is unbiased evaluation)
  application_test              -> unlabelled scoring example (never an evaluation set)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd

from .config import Settings
from .db import connect, read_frame, write_frame
from .explain import reason_codes
from .features import downcast
from .train import load_champion
from .utils import audited_run, timed
from .validate import QualityRunner

log = logging.getLogger(__name__)


def score_population(settings: Settings) -> pd.DataFrame:
    model = load_champion(settings)
    with connect(settings) as conn, timed("load scoring frame"):
        df = downcast(read_frame(conn, """
            SELECT m.*, COALESCE(s.split, 'application_test') AS dataset_split
              FROM analytics.customer_feature_mart m
              LEFT JOIN analytics.model_split s USING (sk_id_curr)"""))
    with timed(f"score {len(df):,} customers with {model.model_version}"):
        raw, cal = model.predict_pd(df)
    scored_at = datetime.now(UTC)
    preds = pd.DataFrame({
        "sk_id_curr": df["sk_id_curr"].astype("int64"),
        "model_version": model.model_version,
        "dataset_split": df["dataset_split"],
        "predicted_pd_raw": raw.astype(float),
        "predicted_pd": cal.astype(float),
        "scored_at": pd.Timestamp(scored_at),
    })
    k = settings.model["explain"]["reason_codes"]
    with timed("reason codes"):
        reasons = reason_codes(model, df, df["sk_id_curr"].to_numpy(), top_k=k)
    reasons.insert(1, "model_version", model.model_version)

    with connect(settings) as conn, audited_run(conn, "score") as run_id:
        write_frame(conn, preds, "analytics.model_predictions", primary_key=["sk_id_curr"])
        write_frame(conn, reasons, "analytics.reason_codes", primary_key=["sk_id_curr", "reason_rank"])
        q = QualityRunner(conn, run_id, "model")
        q.equal_counts("scored_population", "SELECT count(*) FROM analytics.model_predictions",
                       "SELECT count(*) FROM analytics.customer_feature_mart", "analytics.model_predictions",
                       "every mart customer receives a PD")
        q.expression_count("pd_range", "analytics.model_predictions", "predicted_pd",
                           "predicted_pd IS NULL OR predicted_pd < 0 OR predicted_pd > 1", "0 <= PD <= 1")
        q.expression_count("pd_range", "analytics.model_predictions", "predicted_pd_raw",
                           "predicted_pd_raw IS NULL OR predicted_pd_raw < 0 OR predicted_pd_raw > 1", "0 <= raw PD <= 1")
        q.persist()
        q.raise_on_fail()
    log.info("scored %d customers; mean PD by split: %s", len(preds),
             preds.groupby("dataset_split")["predicted_pd"].mean().round(4).to_dict())
    return preds

