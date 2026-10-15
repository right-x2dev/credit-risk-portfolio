-- =============================================================================
-- mart.fact_customer_risk — grain: 1 row per labelled current application (SK_ID_CURR)
-- Population: application_train customers (all splits; dataset_split tells which).
-- Business rules (single source of truth — DAX only aggregates these columns):
--   exposure_proxy = AMT_CREDIT                       (proxy, NOT an observed EAD)
--   base_lgd       = analytics.cfg_risk_parameters    (assumption, NOT an observed LGD)
--   expected_loss  = predicted_pd * exposure_proxy * base_lgd   (scenario metric, NOT a provision)
--   risk grade     = [pd_lower, pd_upper) from analytics.cfg_risk_grade
-- Delinquency bands distinguish "no history" (NULL aggregate) from "0 events".
-- =============================================================================
DROP TABLE IF EXISTS mart.fact_customer_risk CASCADE;
CREATE TABLE mart.fact_customer_risk AS
WITH params AS (SELECT * FROM analytics.cfg_risk_parameters LIMIT 1),
champion AS (
    SELECT model_version_key, model_version FROM mart.dim_model_version WHERE is_champion
),
reasons AS (
    SELECT sk_id_curr,
           max(feature) FILTER (WHERE reason_rank = 1) AS reason_1,
           max(feature) FILTER (WHERE reason_rank = 2) AS reason_2,
           max(feature) FILTER (WHERE reason_rank = 3) AS reason_3
      FROM analytics.reason_codes
     GROUP BY sk_id_curr
)
SELECT
    m.sk_id_curr,
    seg.segment_key,
    g.risk_grade_key,
    c.model_version_key,
    p.dataset_split,
    m.target::int                                                   AS target,
    p.predicted_pd_raw,
    p.predicted_pd,
    LEAST(floor(p.predicted_pd * 50) * 2, 40)::int                  AS pd_bucket_pct,      -- 2pp buckets, 40+ capped
    m.amt_credit                                                    AS exposure_proxy,
    prm.base_lgd,
    p.predicted_pd * m.amt_credit * prm.base_lgd                    AS expected_loss,
    m.amt_income_total,
    m.amt_annuity,
    m.amt_goods_price,
    m.credit_income_ratio,
    m.annuity_income_ratio,
    m.ext_source_mean,
    m.age_years,
    m.employment_years,
    -- repayment / delinquency attributes (Page 3)
    m.has_installments, m.has_bureau, m.has_previous, m.has_credit_card, m.has_pos_cash,
    m.inst_count, m.inst_late_count, m.inst_late_ratio, m.inst_dpd_mean, m.inst_dpd_max,
    m.inst_severe_late_count, m.inst_payment_ratio_mean, m.inst_underpaid_ratio,
    m.pos_dpd_max, m.cc_utilization_mean, m.cc_dpd_max,
    m.bur_count, m.bur_active_count, m.bur_active_debt_credit_ratio, m.bur_bb_worst_status, m.bur_overdue_now_count,
    m.prev_count, m.prev_approved_count, m.prev_refused_count, m.prev_last_status,
    (m.inst_late_count > 0)::int                                    AS is_past_due_history,
    (m.inst_dpd_max >= prm.severe_dpd_days)::int                    AS is_severe_delinquency,
    CASE WHEN m.inst_count IS NULL               THEN 'No instalment history'
         WHEN m.inst_dpd_max = 0                 THEN 'Always on time'
         WHEN m.inst_dpd_max <= 5                THEN '1-5 days'
         WHEN m.inst_dpd_max <= 30               THEN '6-30 days'
         WHEN m.inst_dpd_max <= 90               THEN '31-90 days'
         ELSE '90+ days' END                                        AS max_dpd_band,
    CASE WHEN m.inst_count IS NULL               THEN 0
         WHEN m.inst_dpd_max = 0                 THEN 1
         WHEN m.inst_dpd_max <= 5                THEN 2
         WHEN m.inst_dpd_max <= 30               THEN 3
         WHEN m.inst_dpd_max <= 90               THEN 4
         ELSE 5 END                                                 AS max_dpd_band_sort,
    CASE WHEN m.inst_payment_ratio_mean IS NULL  THEN 'No instalment history'
         WHEN m.inst_payment_ratio_mean < 0.80   THEN '< 80%'
         WHEN m.inst_payment_ratio_mean < 0.95   THEN '80-95%'
         WHEN m.inst_payment_ratio_mean < 1.00   THEN '95-100%'
         ELSE '>= 100%' END                                         AS payment_ratio_band,
    CASE WHEN m.inst_payment_ratio_mean IS NULL  THEN 0
         WHEN m.inst_payment_ratio_mean < 0.80   THEN 1
         WHEN m.inst_payment_ratio_mean < 0.95   THEN 2
         WHEN m.inst_payment_ratio_mean < 1.00   THEN 3
         ELSE 4 END                                                 AS payment_ratio_band_sort,
    CASE WHEN m.cc_utilization_mean IS NULL      THEN 'No card history'
         WHEN m.cc_utilization_mean <= 0         THEN 'Unused (<= 0%)'
         WHEN m.cc_utilization_mean < 0.30       THEN '0-30%'
         WHEN m.cc_utilization_mean < 0.70       THEN '30-70%'
         WHEN m.cc_utilization_mean <= 1.00      THEN '70-100%'
         ELSE 'Over limit (> 100%)' END                             AS utilization_band,
    CASE WHEN m.cc_utilization_mean IS NULL      THEN 0
         WHEN m.cc_utilization_mean <= 0         THEN 1
         WHEN m.cc_utilization_mean < 0.30       THEN 2
         WHEN m.cc_utilization_mean < 0.70       THEN 3
         WHEN m.cc_utilization_mean <= 1.00      THEN 4
         ELSE 5 END                                                 AS utilization_band_sort,
    CASE WHEN m.bur_count IS NULL                THEN 'No bureau record'
         WHEN m.bur_active_count = 0             THEN '0 active'
         WHEN m.bur_active_count = 1             THEN '1 active'
         WHEN m.bur_active_count <= 3            THEN '2-3 active'
         ELSE '4+ active' END                                       AS bureau_active_band,
    CASE WHEN m.bur_count IS NULL                THEN 0
         WHEN m.bur_active_count = 0             THEN 1
         WHEN m.bur_active_count = 1             THEN 2
         WHEN m.bur_active_count <= 3            THEN 3
         ELSE 4 END                                                 AS bureau_active_band_sort,
    CASE WHEN m.bur_active_debt_credit_ratio IS NULL THEN 'No active bureau credit'
         WHEN m.bur_active_debt_credit_ratio < 0.25  THEN '< 25%'
         WHEN m.bur_active_debt_credit_ratio < 0.50  THEN '25-50%'
         WHEN m.bur_active_debt_credit_ratio < 0.75  THEN '50-75%'
         ELSE '>= 75%' END                                          AS bureau_debt_ratio_band,
    CASE WHEN m.bur_active_debt_credit_ratio IS NULL THEN 0
         WHEN m.bur_active_debt_credit_ratio < 0.25  THEN 1
         WHEN m.bur_active_debt_credit_ratio < 0.50  THEN 2
         WHEN m.bur_active_debt_credit_ratio < 0.75  THEN 3
         ELSE 4 END                                                 AS bureau_debt_ratio_band_sort,
    CASE WHEN m.prev_count IS NULL               THEN 'No previous application'
         WHEN m.prev_refused_count > 0           THEN 'Refused at least once'
         WHEN m.prev_approved_count > 0          THEN 'Approved, never refused'
         ELSE 'Canceled / unused only' END                          AS prev_outcome_group,
    CASE WHEN m.prev_count IS NULL               THEN 0
         WHEN m.prev_refused_count > 0           THEN 3
         WHEN m.prev_approved_count > 0          THEN 1
         ELSE 2 END                                                 AS prev_outcome_group_sort,
    r.reason_1, r.reason_2, r.reason_3,
    p.scored_at::date                                               AS score_date
FROM analytics.customer_feature_mart m
JOIN analytics.model_predictions p   USING (sk_id_curr)
JOIN champion c                      ON c.model_version = p.model_version
JOIN staging.customer_segment_attr sa USING (sk_id_curr)
JOIN mart.dim_customer_segment seg
  ON  seg.age_band = sa.age_band AND seg.income_band = sa.income_band AND seg.gender = sa.gender
  AND seg.family_status = sa.family_status AND seg.housing_type = sa.housing_type AND seg.education = sa.education
  AND seg.income_type = sa.income_type AND seg.occupation = sa.occupation AND seg.contract_type = sa.contract_type
  AND seg.children_group = sa.children_group AND seg.region_rating = sa.region_rating
JOIN mart.dim_risk_grade g
  ON p.predicted_pd >= g.pd_lower AND (p.predicted_pd < g.pd_upper OR (g.pd_upper >= 1.0 AND p.predicted_pd <= 1.0))
CROSS JOIN params prm
LEFT JOIN reasons r USING (sk_id_curr)
WHERE m.source = 'train';

ALTER TABLE mart.fact_customer_risk ADD PRIMARY KEY (sk_id_curr);
CREATE INDEX ON mart.fact_customer_risk (risk_grade_key);
CREATE INDEX ON mart.fact_customer_risk (segment_key);
ANALYZE mart.fact_customer_risk;

-- Scenario base, aggregated (segment x risk grade). Used for loss-contribution tables and the SQL
-- reconciliation of DAX scenario measures; the customer-level stress cap MIN(1, PD x stress) is
-- reproduced exactly by v_scenario_sensitivity below.
DROP TABLE IF EXISTS mart.fact_scenario_base CASCADE;
CREATE TABLE mart.fact_scenario_base AS
SELECT segment_key,
       risk_grade_key,
       count(*)                                  AS customers,
       sum(exposure_proxy)                       AS exposure_proxy,
       sum(predicted_pd * exposure_proxy)        AS pd_x_exposure,
       avg(predicted_pd)                         AS avg_pd,
       max(base_lgd)                             AS base_lgd,
       sum(expected_loss)                        AS base_expected_loss
  FROM mart.fact_customer_risk
 GROUP BY segment_key, risk_grade_key;
ALTER TABLE mart.fact_scenario_base ADD PRIMARY KEY (segment_key, risk_grade_key);
