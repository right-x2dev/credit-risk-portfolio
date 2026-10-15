-- =============================================================================
-- Credit card monthly balance -> grain SK_ID_CURR.  MONTHS_BALANCE < 0 only.
-- utilization = AMT_BALANCE / AMT_CREDIT_LIMIT_ACTUAL, only where limit > 0 (0-limit months -> NULL).
-- Negative balances (customer in credit) are kept as-is; utilization can exceed 1 (over-limit).
-- =============================================================================
DROP TABLE IF EXISTS staging.credit_card_agg CASCADE;
CREATE TABLE staging.credit_card_agg AS
WITH base AS (
    SELECT c.*,
           c.amt_balance / NULLIF(c.amt_credit_limit_actual, 0)                       AS utilization,
           c.amt_payment_total_current / NULLIF(c.amt_inst_min_regularity, 0)         AS min_payment_ratio
      FROM raw.credit_card_balance c
     WHERE months_balance < 0
)
SELECT
    sk_id_curr,
    count(*)                                                        AS cc_months,
    count(DISTINCT sk_id_prev)                                      AS cc_card_count,
    avg(amt_credit_limit_actual)                                    AS cc_limit_mean,
    max(amt_credit_limit_actual)                                    AS cc_limit_max,
    avg(amt_balance)                                                AS cc_balance_mean,
    max(amt_balance)                                                AS cc_balance_max,
    avg(utilization)                                                AS cc_utilization_mean,
    max(utilization)                                                AS cc_utilization_max,
    avg(utilization) FILTER (WHERE months_balance >= -6)            AS cc_utilization_mean_6m,
    avg(utilization) FILTER (WHERE months_balance >= -12)           AS cc_utilization_mean_12m,
    avg(amt_drawings_current)                                       AS cc_drawings_mean,
    avg(amt_drawings_atm_current)                                   AS cc_atm_drawings_mean,
    avg(cnt_drawings_current)                                       AS cc_drawings_count_mean,
    avg(min_payment_ratio)                                          AS cc_min_payment_ratio_mean,
    avg((amt_payment_total_current < amt_inst_min_regularity)::int) AS cc_below_min_payment_ratio,
    max(sk_dpd)                                                     AS cc_dpd_max,
    avg((sk_dpd > 0)::int)                                          AS cc_dpd_month_ratio,
    -max(months_balance)                                            AS cc_months_since_last
FROM base
GROUP BY sk_id_curr;
ALTER TABLE staging.credit_card_agg ADD PRIMARY KEY (sk_id_curr);
