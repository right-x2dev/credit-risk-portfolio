-- =============================================================================
-- KPI & reconciliation views. Power BI measures are validated against these (reports/powerbi_validation.md).
-- =============================================================================
CREATE OR REPLACE VIEW mart.v_kpi_portfolio AS
SELECT count(DISTINCT f.sk_id_curr)                                              AS total_customers,
       sum(f.exposure_proxy)                                                     AS total_exposure,
       sum(f.target)                                                             AS observed_difficulty_customers,
       avg(f.target::double precision)                                           AS observed_difficulty_rate,
       avg(f.predicted_pd)                                                       AS average_pd,
       sum(f.predicted_pd * f.exposure_proxy) / NULLIF(sum(f.exposure_proxy), 0) AS exposure_weighted_pd,
       sum(f.expected_loss)                                                      AS base_expected_loss,
       sum(f.expected_loss) / NULLIF(sum(f.exposure_proxy), 0)                   AS el_rate,
       count(*) FILTER (WHERE g.is_high_risk)                                    AS high_risk_customers,
       count(*) FILTER (WHERE g.is_high_risk)::double precision / count(*)       AS high_risk_customer_pct,
       sum(f.exposure_proxy) FILTER (WHERE g.is_high_risk)                       AS high_risk_exposure,
       sum(f.exposure_proxy) FILTER (WHERE g.is_high_risk) / NULLIF(sum(f.exposure_proxy), 0) AS high_risk_exposure_pct,
       sum(f.exposure_proxy) FILTER (WHERE g.is_high_risk AND f.target = 1)
         / NULLIF(sum(f.exposure_proxy) FILTER (WHERE f.target = 1), 0)         AS risk_capture_exposure,
       avg(f.is_past_due_history::double precision)                              AS past_due_customer_pct,
       avg(f.is_severe_delinquency::double precision)                            AS severe_delinquency_customer_pct,
       avg(f.inst_dpd_max)                                                       AS average_max_dpd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY f.inst_dpd_max)               AS median_max_dpd,
       avg(f.inst_payment_ratio_mean)                                            AS average_payment_ratio,
       avg(f.cc_utilization_mean)                                                AS average_credit_utilization
  FROM mart.fact_customer_risk f
  JOIN mart.dim_risk_grade g USING (risk_grade_key);

CREATE OR REPLACE VIEW mart.v_kpi_by_grade AS
SELECT g.risk_grade, g.sort_key,
       count(*)                                     AS customers,
       sum(f.exposure_proxy)                        AS exposure,
       avg(f.target::double precision)              AS observed_difficulty_rate,
       avg(f.predicted_pd)                          AS average_pd,
       sum(f.expected_loss)                         AS base_expected_loss,
       count(*)::double precision / sum(count(*)) OVER ()                      AS customer_share,
       sum(f.exposure_proxy) / sum(sum(f.exposure_proxy)) OVER ()              AS exposure_share
  FROM mart.fact_customer_risk f
  JOIN mart.dim_risk_grade g USING (risk_grade_key)
 GROUP BY g.risk_grade, g.sort_key;

-- PD stress x LGD sensitivity grid, same rule as DAX [Sensitivity EL]:
--   SUM( MIN(1, PD * (1 + stress)) * exposure * lgd )
CREATE OR REPLACE VIEW mart.v_scenario_sensitivity AS
SELECT s.pd_stress, l.lgd,
       sum(LEAST(1.0, f.predicted_pd * (1 + s.pd_stress)) * f.exposure_proxy * l.lgd) AS scenario_expected_loss
  FROM mart.fact_customer_risk f
  CROSS JOIN (SELECT generate_series(-20, 100, 20) / 100.0 AS pd_stress) s
  CROSS JOIN (SELECT generate_series(10, 90, 10) / 100.0 AS lgd) l
 GROUP BY s.pd_stress, l.lgd;

-- refresh metadata shown on every report page
CREATE OR REPLACE VIEW mart.v_report_metadata AS
SELECT (SELECT max(finished_at) FROM audit.pipeline_run WHERE status = 'success')   AS last_refreshed,
       v.model_version, v.algorithm, v.calibration_method, v.selected_threshold, v.trained_at,
       (SELECT count(*) FROM mart.fact_customer_risk)                                 AS population_customers,
       'application_train customers (labelled); PD = proxy PD of Home Credit TARGET'::text AS population_note,
       (SELECT base_lgd FROM analytics.cfg_risk_parameters LIMIT 1)                   AS base_lgd,
       (SELECT is_synthetic FROM analytics.cfg_risk_parameters LIMIT 1)               AS is_synthetic
  FROM mart.dim_model_version v
 WHERE v.is_champion;
