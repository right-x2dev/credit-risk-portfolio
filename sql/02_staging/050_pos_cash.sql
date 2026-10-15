-- =============================================================================
-- POS/CASH monthly balance -> grain SK_ID_CURR.  MONTHS_BALANCE < 0 only (before application).
-- =============================================================================
DROP TABLE IF EXISTS staging.pos_cash_agg CASCADE;
CREATE TABLE staging.pos_cash_agg AS
WITH base AS (
    SELECT p.*,
           row_number() OVER (PARTITION BY sk_id_prev ORDER BY months_balance DESC) AS rn_latest
      FROM raw.pos_cash_balance p
     WHERE months_balance < 0
)
SELECT
    sk_id_curr,
    count(*)                                                        AS pos_months,
    count(DISTINCT sk_id_prev)                                      AS pos_loan_count,
    avg(sk_dpd)                                                     AS pos_dpd_mean,
    max(sk_dpd)                                                     AS pos_dpd_max,
    max(sk_dpd_def)                                                 AS pos_dpd_def_max,
    avg((sk_dpd > 0)::int)                                          AS pos_dpd_month_ratio,
    avg((sk_dpd > 0)::int) FILTER (WHERE months_balance >= -12)     AS pos_dpd_month_ratio_12m,
    avg((sk_dpd > 0)::int) FILTER (WHERE months_balance >= -6)      AS pos_dpd_month_ratio_6m,
    count(DISTINCT sk_id_prev) FILTER (WHERE name_contract_status = 'Completed') AS pos_completed_loan_count,
    avg(cnt_instalment)                                             AS pos_cnt_instalment_mean,
    avg(cnt_instalment_future) FILTER (WHERE rn_latest = 1)         AS pos_instalment_future_latest_mean,
    sum(cnt_instalment_future) FILTER (WHERE rn_latest = 1)         AS pos_instalment_future_latest_sum,
    -max(months_balance)                                            AS pos_months_since_last
FROM base
GROUP BY sk_id_curr;
ALTER TABLE staging.pos_cash_agg ADD PRIMARY KEY (sk_id_curr);
