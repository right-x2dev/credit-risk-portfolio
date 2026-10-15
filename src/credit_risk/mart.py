"""BI mart build (Phase 6): star schema + KPI views, reconciliation checks and the Power BI snapshot.

Power BI connects to PostgreSQL (`mart` schema) in Import mode. For a credential-free first open,
the same tables are also exported as CSV to data/processed/powerbi/ — the PBIP `DataSource` parameter
switches between the two; both carry identical rows.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime

from .config import Settings
from .db import connect, read_frame, run_sql_dir
from .risk import scenario_expected_loss
from .transform import sql_params
from .utils import audited_run, timed, write_json
from .validate import QualityRunner

log = logging.getLogger(__name__)

BI_TABLES = [
    "dim_risk_grade", "dim_model_version", "dim_split", "dim_customer_segment",
    "fact_customer_risk", "fact_scenario_base", "fact_model_evaluation", "fact_model_curve", "fact_calibration",
    "fact_lift", "fact_feature_importance", "fact_segment_performance", "fact_confusion_matrix",
    "v_report_metadata",
]


def mart_checks(q: QualityRunner) -> None:
    f = "mart.fact_customer_risk"
    q.key_unique(f, ["sk_id_curr"])
    q.equal_counts("population_reconciled", f"SELECT count(*) FROM {f}", "SELECT count(*) FROM raw.application_train",
                   f, "fact customers = application_train population")
    q.expression_count("pd_range", f, "predicted_pd", "predicted_pd IS NULL OR predicted_pd < 0 OR predicted_pd > 1",
                       "0 <= predicted_pd <= 1")
    q.expression_count("exposure_valid", f, "exposure_proxy", "exposure_proxy IS NULL OR exposure_proxy <= 0",
                       "exposure_proxy > 0")
    q.expression_count("el_non_negative", f, "expected_loss", "expected_loss IS NULL OR expected_loss < 0",
                       "expected_loss >= 0")
    q.expression_count("risk_grade_assigned", f, "risk_grade_key", "risk_grade_key IS NULL",
                       "every scored customer has a risk grade")
    q.expression_count("segment_assigned", f, "segment_key", "segment_key IS NULL", "every customer has a segment")
    q.expression_count("target_domain", f, "target", "target IS NULL OR target NOT IN (0, 1)", "target in {0,1}")
    q.equal_counts("grade_totals_reconcile",
                   "SELECT round(sum(exposure)::numeric, 2) FROM mart.v_kpi_by_grade",
                   f"SELECT round(sum(exposure_proxy)::numeric, 2) FROM {f}", "mart.v_kpi_by_grade",
                   "sum of exposure over risk grades = total exposure")
    q.equal_counts("scenario_base_reconcile",
                   "SELECT round(sum(base_expected_loss)::numeric, 2) FROM mart.fact_scenario_base",
                   f"SELECT round(sum(expected_loss)::numeric, 2) FROM {f}", "mart.fact_scenario_base",
                   "aggregated scenario base EL = customer-level EL")
    q.equal_counts("el_formula", f"SELECT round(sum(expected_loss)::numeric, 2) FROM {f}",
                   f"SELECT round(sum(predicted_pd * exposure_proxy * base_lgd)::numeric, 2) FROM {f}", f,
                   "EL = PD x Exposure proxy x LGD")
    q.equal_counts("champion_unique", "SELECT count(*) FROM mart.dim_model_version WHERE is_champion",
                   "SELECT 1", "mart.dim_model_version", "exactly one champion model version")
    q.equal_counts("calibration_counts_reconcile",
                   "SELECT sum(customers) FROM mart.fact_calibration c JOIN mart.dim_model_version v "
                   "USING (model_version_key) WHERE v.is_champion AND dataset_split='holdout' AND score_type='calibrated'",
                   f"SELECT count(*) FROM {f} WHERE dataset_split='holdout'", "mart.fact_calibration",
                   "calibration bins sum to the holdout sample size")
    q.equal_counts("grade_coverage",
                   f"SELECT count(*) FROM {f} f JOIN mart.dim_risk_grade g USING (risk_grade_key) "
                   "WHERE f.predicted_pd < g.pd_lower OR f.predicted_pd > g.pd_upper", "SELECT 0", f,
                   "PD lies inside its grade boundaries")


def reconciliation_snapshot(conn) -> dict:
    kpi = read_frame(conn, "SELECT * FROM mart.v_kpi_portfolio").iloc[0].to_dict()
    by_grade = read_frame(conn, "SELECT * FROM mart.v_kpi_by_grade ORDER BY sort_key").to_dict(orient="records")
    sens = read_frame(conn, "SELECT * FROM mart.v_scenario_sensitivity ORDER BY pd_stress, lgd")
    # independent Python re-computation of one scenario cell as a cross-check of the SQL rule
    f = read_frame(conn, "SELECT predicted_pd, exposure_proxy FROM mart.fact_customer_risk")
    py_val = scenario_expected_loss(f["predicted_pd"].to_numpy(), f["exposure_proxy"].to_numpy(), 0.45, 0.2)
    sql_val = float(sens.query("abs(pd_stress-0.2)<1e-9 and abs(lgd-0.45)<1e-9")["scenario_expected_loss"].iloc[0]) \
        if ((sens["lgd"] - 0.45).abs() < 1e-9).any() else None
    return {"generated_at": datetime.now(UTC).isoformat(), "portfolio": kpi, "by_grade": by_grade,
            "sensitivity_grid": sens.to_dict(orient="records"),
            "python_check_pd_stress_20_lgd_45": py_val, "sql_check_pd_stress_20_lgd_45": sql_val}


def build_mart(settings: Settings) -> None:
    with connect(settings) as conn, audited_run(conn, "mart") as run_id:
        with timed("mart SQL"):
            run_sql_dir(conn, settings.sql_dir / "04_mart", sql_params(settings))
            conn.commit()
        q = QualityRunner(conn, run_id, "mart")
        mart_checks(q)
        q.persist()
        log.info("mart quality summary: %s", q.summary())
        q.raise_on_fail()
        snap = reconciliation_snapshot(conn)
    write_json(settings.reports_dir / "sql_reconciliation.json", snap)
    p = snap["portfolio"]
    log.info("portfolio: customers=%s exposure=%.0f obs_rate=%.4f ew_pd=%.4f EL=%.0f high_risk_exp=%.3f",
             p["total_customers"], p["total_exposure"], p["observed_difficulty_rate"], p["exposure_weighted_pd"],
             p["base_expected_loss"], p["high_risk_exposure_pct"])


def export_powerbi_snapshot(settings: Settings) -> None:
    """Export mart tables as UTF-8 CSV for the credential-free Power BI data source mode."""
    snapshot_dir = settings.export_dir
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with connect(settings) as conn:
        for name in BI_TABLES:
            path = snapshot_dir / f"{name}.csv"
            cols = conn.execute("SELECT column_name, data_type FROM information_schema.columns "
                                "WHERE table_schema='mart' AND table_name=%s ORDER BY ordinal_position", (name,)).fetchall()
            # timestamps without offset and booleans as true/false so Power Query parses them culture-independently
            select = ", ".join(
                f"to_char({c}, 'YYYY-MM-DD HH24:MI:SS') AS {c}" if t.startswith("timestamp")
                else f"CASE WHEN {c} THEN 'true' WHEN NOT {c} THEN 'false' END AS {c}" if t == "boolean"
                else c for c, t in cols)
            with path.open("wb") as fh, conn.cursor().copy(
                    f"COPY (SELECT {select} FROM mart.{name}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
                for chunk in copy:
                    fh.write(chunk)
            with path.open(encoding="utf-8") as fh:
                rows = sum(1 for _ in csv.reader(fh)) - 1
            manifest[name] = {"rows": rows, "bytes": path.stat().st_size}
            log.info("export mart.%-28s %9d rows", name, rows)
    write_json(snapshot_dir / "_manifest.json", {"exported_at": datetime.now(UTC).isoformat(),
                                                 "tables": manifest})


__all__ = ["build_mart", "export_powerbi_snapshot"]
