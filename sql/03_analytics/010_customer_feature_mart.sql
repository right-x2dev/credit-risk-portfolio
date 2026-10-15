-- =============================================================================
-- analytics.customer_feature_mart — grain: exactly 1 row per SK_ID_CURR (train + test)
--
-- application (1 row/customer)
--   LEFT JOIN application_derived  (1:1)
--   LEFT JOIN bureau_agg           (1:0..1, already aggregated to SK_ID_CURR)
--   LEFT JOIN previous_agg         (1:0..1)
--   LEFT JOIN installments_agg     (1:0..1)
--   LEFT JOIN pos_cash_agg         (1:0..1)
--   LEFT JOIN credit_card_agg      (1:0..1)
-- Every right-hand side has a PRIMARY KEY on sk_id_curr, so the joins cannot amplify rows.
-- `USING (sk_id_curr)` merges the key into one column in SELECT *.
-- NULL in an aggregate means "no history in that source" (distinct from 0 events); the has_* flags
-- make that explicit.
-- =============================================================================
DROP TABLE IF EXISTS analytics.customer_feature_mart CASCADE;
CREATE TABLE analytics.customer_feature_mart AS
SELECT
    m.*,
    (b.bur_count  IS NOT NULL)::int  AS has_bureau,
    (p.prev_count IS NOT NULL)::int  AS has_previous,
    (i.inst_count IS NOT NULL)::int  AS has_installments,
    (pc.pos_months IS NOT NULL)::int AS has_pos_cash,
    (c.cc_months  IS NOT NULL)::int  AS has_credit_card,
    '{{feature_version}}'::text      AS feature_version,
    now()                            AS built_at
FROM (
    SELECT *
      FROM staging.application
      LEFT JOIN staging.application_derived USING (sk_id_curr)
      LEFT JOIN staging.bureau_agg          USING (sk_id_curr)
      LEFT JOIN staging.previous_agg        USING (sk_id_curr)
      LEFT JOIN staging.installments_agg    USING (sk_id_curr)
      LEFT JOIN staging.pos_cash_agg        USING (sk_id_curr)
      LEFT JOIN staging.credit_card_agg     USING (sk_id_curr)
) m
LEFT JOIN staging.bureau_agg       b  USING (sk_id_curr)
LEFT JOIN staging.previous_agg     p  USING (sk_id_curr)
LEFT JOIN staging.installments_agg i  USING (sk_id_curr)
LEFT JOIN staging.pos_cash_agg     pc USING (sk_id_curr)
LEFT JOIN staging.credit_card_agg  c  USING (sk_id_curr);

-- fails the pipeline if the grain is violated
ALTER TABLE analytics.customer_feature_mart ADD PRIMARY KEY (sk_id_curr);
CREATE INDEX ON analytics.customer_feature_mart (source);
ANALYZE analytics.customer_feature_mart;
