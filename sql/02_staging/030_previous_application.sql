-- =============================================================================
-- Previous Home Credit applications -> grain SK_ID_CURR
-- Sentinel {{previous_days_sentinel}} in DAYS_FIRST_DRAWING/FIRST_DUE/LAST_DUE*/TERMINATION -> NULL.
-- Leakage guard: DAYS_DECISION must be <= 0. Dates of *actual* events after the current application
-- (DAYS_LAST_DUE > 0, DAYS_TERMINATION > 0) are set to NULL: unknown at decision time.
-- =============================================================================
DROP TABLE IF EXISTS staging.previous_application CASCADE;
CREATE TABLE staging.previous_application AS
SELECT
    p.*,
    NULLIF(p.days_first_drawing, {{previous_days_sentinel}})            AS days_first_drawing_clean,
    NULLIF(p.days_first_due, {{previous_days_sentinel}})                AS days_first_due_clean,
    NULLIF(p.days_last_due_1st_version, {{previous_days_sentinel}})     AS days_last_due_1st_version_clean,
    CASE WHEN p.days_last_due = {{previous_days_sentinel}} OR p.days_last_due > 0 THEN NULL
         ELSE p.days_last_due END                                       AS days_last_due_clean,
    CASE WHEN p.days_termination = {{previous_days_sentinel}} OR p.days_termination > 0 THEN NULL
         ELSE p.days_termination END                                    AS days_termination_clean,
    p.amt_credit / NULLIF(p.amt_application, 0)                         AS credit_application_ratio
FROM raw.previous_application p
WHERE p.days_decision <= 0;
ALTER TABLE staging.previous_application ADD PRIMARY KEY (sk_id_prev);

DROP TABLE IF EXISTS staging.previous_agg CASCADE;
CREATE TABLE staging.previous_agg AS
WITH ranked AS (
    SELECT p.*, row_number() OVER (PARTITION BY sk_id_curr ORDER BY days_decision DESC, sk_id_prev DESC) AS rn
      FROM staging.previous_application p
)
SELECT
    sk_id_curr,
    count(*)                                                                AS prev_count,
    count(*) FILTER (WHERE name_contract_status = 'Approved')               AS prev_approved_count,
    count(*) FILTER (WHERE name_contract_status = 'Refused')                AS prev_refused_count,
    count(*) FILTER (WHERE name_contract_status = 'Canceled')               AS prev_canceled_count,
    count(*) FILTER (WHERE name_contract_status = 'Unused offer')           AS prev_unused_count,
    avg((name_contract_status = 'Approved')::int)                           AS prev_approved_ratio,
    avg((name_contract_status = 'Refused')::int)                            AS prev_refused_ratio,
    avg((name_contract_status = 'Refused')::int) FILTER (WHERE days_decision >= -730) AS prev_refused_ratio_2y,
    avg(amt_application)                                                    AS prev_amt_application_mean,
    max(amt_application)                                                    AS prev_amt_application_max,
    sum(amt_application)                                                    AS prev_amt_application_sum,
    avg(amt_credit) FILTER (WHERE name_contract_status = 'Approved')        AS prev_amt_credit_approved_mean,
    max(amt_credit) FILTER (WHERE name_contract_status = 'Approved')        AS prev_amt_credit_approved_max,
    sum(amt_credit) FILTER (WHERE name_contract_status = 'Approved')        AS prev_amt_credit_approved_sum,
    avg(credit_application_ratio) FILTER (WHERE name_contract_status = 'Approved') AS prev_credit_application_ratio_mean,
    avg(amt_annuity)                                                        AS prev_amt_annuity_mean,
    avg(amt_down_payment)                                                   AS prev_amt_down_payment_mean,
    avg(rate_down_payment)                                                  AS prev_rate_down_payment_mean,
    avg(rate_interest_primary)                                              AS prev_rate_interest_primary_mean,
    max(rate_interest_privileged)                                           AS prev_rate_interest_privileged_max,
    avg(cnt_payment)                                                        AS prev_cnt_payment_mean,
    -max(days_decision)                                                     AS prev_days_since_last,
    -max(days_decision) FILTER (WHERE name_contract_status = 'Refused')      AS prev_days_since_last_refused,
    -max(days_decision) FILTER (WHERE name_contract_status = 'Approved')     AS prev_days_since_last_approved,
    count(*) FILTER (WHERE name_contract_type = 'Cash loans')               AS prev_cash_count,
    count(*) FILTER (WHERE name_contract_type = 'Consumer loans')           AS prev_consumer_count,
    count(*) FILTER (WHERE name_contract_type = 'Revolving loans')          AS prev_revolving_count,
    count(*) FILTER (WHERE name_client_type = 'New')                        AS prev_new_client_count,
    count(*) FILTER (WHERE name_yield_group = 'high')                       AS prev_high_yield_count,
    avg(nflag_insured_on_approval)                                          AS prev_insured_ratio,
    max(CASE WHEN rn = 1 THEN name_contract_status END)                     AS prev_last_status,
    max(CASE WHEN rn = 1 THEN name_contract_type END)                       AS prev_last_contract_type
FROM ranked
GROUP BY sk_id_curr;
ALTER TABLE staging.previous_agg ADD PRIMARY KEY (sk_id_curr);
