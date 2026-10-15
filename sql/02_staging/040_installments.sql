-- =============================================================================
-- Installments: row grain = payment event. Several payment rows can belong to one scheduled
-- instalment (partial payments), so we aggregate in 2 steps:
--   step 1 -> grain (SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER, DAYS_INSTALMENT)
--   step 2 -> grain SK_ID_CURR
-- Definitions (spec §10.5, applied per scheduled instalment):
--   days_past_due   = GREATEST(last DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT, 0)
--   days_before_due = GREATEST(DAYS_INSTALMENT - first DAYS_ENTRY_PAYMENT, 0)
--   payment_ratio   = total paid / NULLIF(AMT_INSTALMENT, 0)
--   payment_diff    = AMT_INSTALMENT - total paid
--   late            = days_past_due > 0 ; severe_late = days_past_due >= {{severe_dpd_days}}
-- Leakage guard: only instalments due and payments made before the application (<= 0).
-- =============================================================================
DROP TABLE IF EXISTS staging.installment_level CASCADE;
CREATE TABLE staging.installment_level AS
SELECT
    sk_id_prev,
    min(sk_id_curr)                         AS sk_id_curr,
    num_instalment_version,
    num_instalment_number,
    days_instalment,
    max(amt_instalment)                     AS amt_instalment,
    sum(amt_payment)                        AS amt_paid,
    min(days_entry_payment)                 AS first_payment_day,
    max(days_entry_payment)                 AS last_payment_day,
    count(*)                                AS payment_rows,
    count(DISTINCT sk_id_curr)              AS n_curr
FROM raw.installments_payments
WHERE days_instalment <= 0
  AND COALESCE(days_entry_payment, 0) <= 0
GROUP BY sk_id_prev, num_instalment_version, num_instalment_number, days_instalment;

ALTER TABLE staging.installment_level ADD COLUMN days_past_due double precision;
ALTER TABLE staging.installment_level ADD COLUMN days_before_due double precision;
ALTER TABLE staging.installment_level ADD COLUMN payment_ratio double precision;
ALTER TABLE staging.installment_level ADD COLUMN payment_diff double precision;
UPDATE staging.installment_level
   SET days_past_due   = GREATEST(last_payment_day - days_instalment, 0),
       days_before_due = GREATEST(days_instalment - first_payment_day, 0),
       payment_ratio   = amt_paid / NULLIF(amt_instalment, 0),
       payment_diff    = amt_instalment - COALESCE(amt_paid, 0);

DROP TABLE IF EXISTS staging.installments_agg CASCADE;
CREATE TABLE staging.installments_agg AS
SELECT
    sk_id_curr,
    count(*)                                                                    AS inst_count,
    count(DISTINCT sk_id_prev)                                                  AS inst_loan_count,
    count(*) FILTER (WHERE days_past_due > 0)                                   AS inst_late_count,
    avg((days_past_due > 0)::int)                                               AS inst_late_ratio,
    avg(days_past_due)                                                          AS inst_dpd_mean,
    max(days_past_due)                                                          AS inst_dpd_max,
    count(*) FILTER (WHERE days_past_due >= {{severe_dpd_days}})                AS inst_severe_late_count,
    avg((days_past_due >= {{severe_dpd_days}})::int)                            AS inst_severe_late_ratio,
    avg(days_before_due)                                                        AS inst_dbd_mean,
    count(*) FILTER (WHERE amt_instalment > 0 AND payment_diff > 0.01 * amt_instalment) AS inst_underpaid_count,
    avg((amt_instalment > 0 AND payment_diff > 0.01 * amt_instalment)::int)    AS inst_underpaid_ratio,
    sum(GREATEST(payment_diff, 0))                                              AS inst_underpaid_amt_sum,
    avg(payment_ratio)                                                          AS inst_payment_ratio_mean,
    min(payment_ratio)                                                          AS inst_payment_ratio_min,
    count(*) FILTER (WHERE last_payment_day IS NULL)                            AS inst_missed_count,
    sum(amt_instalment)                                                         AS inst_amt_instalment_sum,
    avg((days_past_due > 0)::int) FILTER (WHERE days_instalment >= -180)        AS inst_late_ratio_6m,
    avg((days_past_due > 0)::int) FILTER (WHERE days_instalment >= -365)        AS inst_late_ratio_12m,
    avg(days_past_due)            FILTER (WHERE days_instalment >= -365)        AS inst_dpd_mean_12m,
    avg(payment_ratio)            FILTER (WHERE days_instalment >= -365)        AS inst_payment_ratio_mean_12m,
    -max(days_instalment)                                                       AS inst_days_since_last_due
FROM staging.installment_level
GROUP BY sk_id_curr;
ALTER TABLE staging.installments_agg ADD PRIMARY KEY (sk_id_curr);
