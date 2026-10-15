"""SQL data-quality tests (spec §17.2) against the PROJECT database (whatever data is currently loaded).
Skipped if the database or the mart has not been built yet."""

from __future__ import annotations

import pytest

from credit_risk import db


@pytest.fixture(scope="module")
def conn(project_settings):
    try:
        c = db.psycopg.connect(project_settings.conninfo, connect_timeout=3)
    except Exception:
        pytest.skip("project database not reachable")
    if db.scalar(c, "SELECT to_regclass('mart.fact_customer_risk')") is None:
        pytest.skip("mart not built yet (run ./scripts/run_all.ps1)")
    yield c
    c.close()


def q(conn, sql):
    return db.scalar(conn, sql)


@pytest.mark.parametrize("table", ["application_train", "application_test", "bureau", "bureau_balance",
                                   "previous_application", "pos_cash_balance", "credit_card_balance",
                                   "installments_payments"])
def test_raw_row_count_positive(conn, table):
    assert q(conn, f"SELECT count(*) FROM raw.{table}") > 0


def test_core_keys_unique_not_null(conn):
    assert q(conn, "SELECT count(*) - count(DISTINCT sk_id_curr) FROM raw.application_train") == 0
    assert q(conn, "SELECT count(*) FROM raw.application_train WHERE sk_id_curr IS NULL") == 0
    assert q(conn, "SELECT count(*) - count(DISTINCT sk_id_bureau) FROM raw.bureau") == 0
    assert q(conn, "SELECT count(*) - count(DISTINCT sk_id_prev) FROM raw.previous_application") == 0


def test_target_accepted_values(conn):
    assert q(conn, "SELECT count(*) FROM raw.application_train WHERE target IS NULL OR target NOT IN (0,1)") == 0


def test_feature_mart_one_row_per_customer(conn):
    assert q(conn, "SELECT count(*) - count(DISTINCT sk_id_curr) FROM analytics.customer_feature_mart") == 0
    assert q(conn, "SELECT count(*) FROM analytics.customer_feature_mart") == q(
        conn, "SELECT (SELECT count(*) FROM raw.application_train) + (SELECT count(*) FROM raw.application_test)")


def test_mart_population_matches(conn):
    assert q(conn, "SELECT count(DISTINCT sk_id_curr) FROM mart.fact_customer_risk") == q(
        conn, "SELECT count(*) FROM raw.application_train")


def test_pd_exposure_el_ranges(conn):
    assert q(conn, "SELECT count(*) FROM mart.fact_customer_risk WHERE predicted_pd < 0 OR predicted_pd > 1 "
                   "OR predicted_pd IS NULL") == 0
    assert q(conn, "SELECT count(*) FROM mart.fact_customer_risk WHERE exposure_proxy <= 0 OR exposure_proxy IS NULL") == 0
    assert q(conn, "SELECT count(*) FROM mart.fact_customer_risk WHERE expected_loss < 0") == 0
    assert q(conn, "SELECT count(*) FROM mart.fact_customer_risk WHERE risk_grade_key IS NULL") == 0


def test_referential_integrity_star_schema(conn):
    for dim, key in [("dim_risk_grade", "risk_grade_key"), ("dim_customer_segment", "segment_key"),
                     ("dim_model_version", "model_version_key")]:
        assert q(conn, f"SELECT count(*) FROM mart.fact_customer_risk f LEFT JOIN mart.{dim} d USING ({key}) "
                       f"WHERE d.{key} IS NULL") == 0
    assert q(conn, "SELECT count(*) FROM mart.fact_customer_risk f LEFT JOIN mart.dim_split d USING (dataset_split) "
                   "WHERE d.dataset_split IS NULL") == 0


def test_grade_totals_equal_portfolio(conn):
    a = q(conn, "SELECT round(sum(base_expected_loss)::numeric, 2) FROM mart.v_kpi_by_grade")
    b = q(conn, "SELECT round(base_expected_loss::numeric, 2) FROM mart.v_kpi_portfolio")
    assert a == b


def test_no_failed_quality_checks_in_latest_runs(conn):
    fails = q(conn, """SELECT count(*) FROM audit.data_quality_result
                        WHERE status = 'fail'
                          AND run_id IN (SELECT max(run_id) FROM audit.data_quality_result GROUP BY layer)""")
    assert fails == 0
