"""Reproducible stratified Train / Validation / Holdout split (spec §12.2).

Only application_train customers are split. application_test is an *unlabelled scoring example*,
never an evaluation set. The split is stored in analytics.model_split so every downstream step
(and Power BI) uses exactly the same assignment.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from .config import Settings
from .db import connect, read_frame, write_frame
from .utils import audited_run
from .validate import QualityRunner

log = logging.getLogger(__name__)


class SplitError(RuntimeError):
    pass


def make_split(ids: np.ndarray, y: np.ndarray, train: float, validation: float, holdout: float,
               seed: int) -> pd.DataFrame:
    if not np.isclose(train + validation + holdout, 1.0):
        raise SplitError("split shares must sum to 1")
    if len(np.unique(ids)) != len(ids):
        raise SplitError("duplicate customer ids before split")
    id_tr, id_rest, y_tr, y_rest = train_test_split(ids, y, test_size=validation + holdout, stratify=y,
                                                    random_state=seed)
    id_va, id_ho = train_test_split(id_rest, test_size=holdout / (validation + holdout), stratify=y_rest,
                                    random_state=seed)
    out = pd.concat([pd.DataFrame({"sk_id_curr": id_tr, "split": "train"}),
                     pd.DataFrame({"sk_id_curr": id_va, "split": "validation"}),
                     pd.DataFrame({"sk_id_curr": id_ho, "split": "holdout"})], ignore_index=True)
    assert_disjoint(out)
    return out


def assert_disjoint(split_df: pd.DataFrame) -> None:
    sets = {s: set(g["sk_id_curr"]) for s, g in split_df.groupby("split")}
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            inter = sets[a] & sets[b]
            if inter:
                raise SplitError(f"{len(inter)} customers appear in both {a} and {b}")


def create_split(settings: Settings) -> pd.DataFrame:
    cfg = settings.model["split"]
    with connect(settings) as conn, audited_run(conn, "split") as run_id:
        base = read_frame(conn, "SELECT sk_id_curr, target FROM analytics.customer_feature_mart "
                                "WHERE source = 'train' ORDER BY sk_id_curr")
        split = make_split(base["sk_id_curr"].to_numpy(), base["target"].to_numpy(), cfg["train"],
                           cfg["validation"], cfg["holdout"], settings.random_seed)
        split["split_seed"] = settings.random_seed
        write_frame(conn, split, "analytics.model_split", primary_key=["sk_id_curr"])
        q = QualityRunner(conn, run_id, "model")
        q.equal_counts("split_covers_population", "SELECT count(*) FROM analytics.model_split",
                       "SELECT count(*) FROM analytics.customer_feature_mart WHERE source='train'",
                       "analytics.model_split", "every labelled customer is assigned to exactly one split")
        q.key_unique("analytics.model_split", ["sk_id_curr"])
        rates = (split.merge(base, on="sk_id_curr").groupby("split")["target"].agg(["size", "mean"]))
        q.add("split_stratification", "analytics.model_split", "target",
              "pass" if rates["mean"].max() - rates["mean"].min() < 0.005 else "warn",
              float(rates["mean"].max() - rates["mean"].min()), "|observed rate difference between splits| < 0.5pp",
              rates=rates.to_dict())
        q.persist()
        q.raise_on_fail()
        log.info("split sizes: %s", rates.to_dict())
    return split
