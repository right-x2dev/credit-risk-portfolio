"""Staging layer build (spec Phase 2): runs sql/02_staging in order and verifies grains,
filters and join cardinalities. Results are persisted to audit.data_quality_result."""

from __future__ import annotations

import logging

from .config import Settings
from .db import connect, run_sql_dir
from .utils import audited_run, timed
from .validate import QualityRunner

log = logging.getLogger(__name__)

# aggregate table -> grain key
STAGING_GRAINS = {
    "staging.application": ["sk_id_curr"],
    "staging.application_derived": ["sk_id_curr"],
    "staging.bureau_balance_by_bureau": ["sk_id_bureau"],
    "staging.bureau": ["sk_id_bureau"],
    "staging.bureau_agg": ["sk_id_curr"],
    "staging.previous_application": ["sk_id_prev"],
    "staging.previous_agg": ["sk_id_curr"],
    "staging.installment_level": ["sk_id_prev", "num_instalment_version", "num_instalment_number", "days_instalment"],
    "staging.installments_agg": ["sk_id_curr"],
    "staging.pos_cash_agg": ["sk_id_curr"],
    "staging.credit_card_agg": ["sk_id_curr"],
}


def sql_params(settings: Settings) -> dict:
    q = settings.project["quality"]
    return {
        "days_employed_sentinel": q["days_employed_sentinel"],
        "previous_days_sentinel": q["previous_days_sentinel"],
        "severe_dpd_days": settings.project["delinquency"]["severe_dpd_days"],
        "feature_version": settings.feature_version,
    }


def staging_checks(q: QualityRunner) -> None:
    for table, keys in STAGING_GRAINS.items():
        q.row_count_positive(table)
        q.key_unique(table, keys, severity="fail")

    q.equal_counts("population_preserved",
                   "SELECT count(*) FROM staging.application",
                   "SELECT (SELECT count(*) FROM raw.application_train) + (SELECT count(*) FROM raw.application_test)",
                   "staging.application", "staging.application rows = train + test rows")
    q.equal_counts("population_preserved",
                   "SELECT count(*) FROM staging.application_derived",
                   "SELECT count(*) FROM staging.application",
                   "staging.application_derived", "derived rows = application rows")

    # rows removed by leakage filters (informational / warn when large)
    for check, raw_q, stg_q, table in [
        ("leakage_filter_removed_rows", "SELECT count(*) FROM raw.bureau", "SELECT count(*) FROM staging.bureau",
         "staging.bureau"),
        ("leakage_filter_removed_rows", "SELECT count(*) FROM raw.previous_application",
         "SELECT count(*) FROM staging.previous_application", "staging.previous_application"),
    ]:
        removed = q.scalar(raw_q) - q.scalar(stg_q)
        total = max(q.scalar(raw_q), 1)
        q.add(check, table, None, "warn" if removed / total > 0.01 else "pass", removed,
              "records dated after the application are excluded (warn if > 1%)", ratio=removed / total)

    # an instalment row must belong to exactly one customer
    q.expression_count("instalment_single_customer", "staging.installment_level", "n_curr", "n_curr > 1",
                       "each scheduled instalment maps to one SK_ID_CURR")
    # orphan customers in history tables that have no current application (they simply never join)
    for agg in ["bureau_agg", "previous_agg", "installments_agg", "pos_cash_agg", "credit_card_agg"]:
        n = q.scalar(f"SELECT count(*) FROM staging.{agg} g LEFT JOIN staging.application a USING (sk_id_curr) "
                     "WHERE a.sk_id_curr IS NULL")
        tot = max(q.scalar(f"SELECT count(*) FROM staging.{agg}"), 1)
        q.add("orphan_customers", f"staging.{agg}", "sk_id_curr", "warn" if n / tot > 0.2 else "pass", n,
              "aggregated customers without a current application (not joined; warn if > 20%)", ratio=n / tot)
    q.expression_count("non_negative", "staging.installment_level", "days_past_due", "days_past_due < 0",
                       "days_past_due >= 0")
    q.expression_count("non_negative", "staging.application_derived", "credit_income_ratio",
                       "credit_income_ratio < 0", "credit_income_ratio >= 0")


def build_staging(settings: Settings) -> None:
    with connect(settings) as conn, audited_run(conn, "staging", str(settings.data_dir)) as run_id:
        with timed("staging SQL"):
            run_sql_dir(conn, settings.sql_dir / "02_staging", sql_params(settings))
            conn.commit()
        q = QualityRunner(conn, run_id, "staging")
        staging_checks(q)
        q.persist()
        log.info("staging quality summary: %s", q.summary())
        q.raise_on_fail()
