-- =============================================================================
-- Bureau history: 2-step aggregation (spec §5.3)
--   step 1  bureau_balance  -> grain SK_ID_BUREAU
--   step 2  bureau (+step1) -> grain SK_ID_CURR
-- Leakage guards:
--   * bureau_balance month 0 (the application month) is excluded; only MONTHS_BALANCE < 0 is used
--   * bureau records with DAYS_CREDIT > 0, DAYS_ENDDATE_FACT > 0 or DAYS_CREDIT_UPDATE > 0 describe
--     events after the current application and are excluded (counts are logged by the staging checks)
-- STATUS codes: C closed, X unknown, 0 no DPD, 1 = 1-30 DPD, 2 = 31-60, 3 = 61-90, 4 = 91-120, 5 = 120+/sold
-- =============================================================================
DROP TABLE IF EXISTS staging.bureau_balance_by_bureau CASCADE;
CREATE TABLE staging.bureau_balance_by_bureau AS
SELECT
    sk_id_bureau,
    count(*)                                                              AS bb_months,
    count(*) FILTER (WHERE status IN ('1','2','3','4','5'))               AS bb_dpd_months,
    count(*) FILTER (WHERE status IN ('2','3','4','5'))                   AS bb_dpd30_months,
    count(*) FILTER (WHERE status = 'X')                                  AS bb_unknown_months,
    max(CASE WHEN status IN ('0','1','2','3','4','5') THEN status::int END) AS bb_worst_status,
    count(*) FILTER (WHERE months_balance >= -12)                         AS bb_months_12m,
    count(*) FILTER (WHERE months_balance >= -12 AND status IN ('1','2','3','4','5')) AS bb_dpd_months_12m
FROM raw.bureau_balance
WHERE months_balance < 0
GROUP BY sk_id_bureau;
ALTER TABLE staging.bureau_balance_by_bureau ADD PRIMARY KEY (sk_id_bureau);

DROP TABLE IF EXISTS staging.bureau CASCADE;
CREATE TABLE staging.bureau AS
SELECT b.*, bb.bb_months, bb.bb_dpd_months, bb.bb_dpd30_months, bb.bb_unknown_months,
       bb.bb_worst_status, bb.bb_months_12m, bb.bb_dpd_months_12m
  FROM raw.bureau b
  LEFT JOIN staging.bureau_balance_by_bureau bb USING (sk_id_bureau)
 WHERE b.days_credit <= 0
   AND COALESCE(b.days_enddate_fact, 0) <= 0
   AND COALESCE(b.days_credit_update, 0) <= 0;
ALTER TABLE staging.bureau ADD PRIMARY KEY (sk_id_bureau);

DROP TABLE IF EXISTS staging.bureau_agg CASCADE;
CREATE TABLE staging.bureau_agg AS
SELECT
    sk_id_curr,
    count(*)                                                            AS bur_count,
    count(*) FILTER (WHERE credit_active = 'Active')                    AS bur_active_count,
    count(*) FILTER (WHERE credit_active = 'Closed')                    AS bur_closed_count,
    count(*) FILTER (WHERE credit_active IN ('Bad debt','Sold'))        AS bur_bad_debt_sold_count,
    avg((credit_active = 'Active')::int)                                AS bur_active_ratio,
    sum(amt_credit_sum)                                                 AS bur_credit_sum_total,
    avg(amt_credit_sum)                                                 AS bur_credit_sum_mean,
    max(amt_credit_sum)                                                 AS bur_credit_sum_max,
    sum(amt_credit_sum_debt)                                            AS bur_debt_total,
    sum(amt_credit_sum_debt) FILTER (WHERE credit_active = 'Active')    AS bur_active_debt_total,
    sum(amt_credit_sum_debt) FILTER (WHERE credit_active = 'Active')
        / NULLIF(sum(amt_credit_sum) FILTER (WHERE credit_active = 'Active'), 0) AS bur_active_debt_credit_ratio,
    sum(amt_credit_sum_limit)                                           AS bur_credit_limit_total,
    sum(amt_credit_sum_overdue)                                         AS bur_overdue_total,
    max(amt_credit_max_overdue)                                         AS bur_max_overdue_amt,
    max(credit_day_overdue)                                             AS bur_day_overdue_max,
    count(*) FILTER (WHERE credit_day_overdue > 0)                      AS bur_overdue_now_count,
    sum(cnt_credit_prolong)                                             AS bur_prolong_total,
    -max(days_credit)                                                   AS bur_days_since_last_credit,
    -min(days_credit)                                                   AS bur_days_since_first_credit,
    max(days_credit_enddate)                                            AS bur_enddate_max,
    avg(days_credit_enddate) FILTER (WHERE credit_active = 'Active')    AS bur_active_enddate_mean,
    -max(days_credit_update)                                            AS bur_days_since_update,
    count(*) FILTER (WHERE credit_type = 'Credit card')                 AS bur_credit_card_count,
    count(*) FILTER (WHERE credit_type IN ('Mortgage','Car loan'))      AS bur_secured_count,
    count(*) FILTER (WHERE credit_type = 'Microloan')                   AS bur_microloan_count,
    count(DISTINCT credit_type)                                         AS bur_credit_type_count,
    sum(amt_annuity)                                                    AS bur_annuity_total,
    -- bureau_balance (step 1) rolled up
    count(bb_months)                                                    AS bur_bb_credit_count,
    sum(bb_months)                                                      AS bur_bb_months_total,
    sum(bb_dpd_months)                                                  AS bur_bb_dpd_months_total,
    sum(bb_dpd30_months)                                                AS bur_bb_dpd30_months_total,
    sum(bb_dpd_months)::double precision / NULLIF(sum(bb_months) - sum(bb_unknown_months), 0) AS bur_bb_dpd_month_ratio,
    max(bb_worst_status)                                                AS bur_bb_worst_status,
    sum(bb_dpd_months_12m)                                              AS bur_bb_dpd_months_12m
FROM staging.bureau
GROUP BY sk_id_curr;
ALTER TABLE staging.bureau_agg ADD PRIMARY KEY (sk_id_curr);
