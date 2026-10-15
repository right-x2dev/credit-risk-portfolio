-- =============================================================================
-- staging.application  — grain: 1 row per SK_ID_CURR (train + test, flagged by `source`)
-- Cleaning rules (raw columns stay untouched in raw.*):
--   * CODE_GENDER 'XNA' and NAME_FAMILY_STATUS 'Unknown' -> NULL (a handful of rows in the real data)
--   * DAYS_EMPLOYED sentinel {{days_employed_sentinel}} -> days_employed_clean NULL + days_employed_anom flag
--     (the original days_employed column is preserved for audit)
-- =============================================================================
DROP TABLE IF EXISTS staging.application CASCADE;

DO $$
DECLARE cols text;
BEGIN
    SELECT string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal_position)
      INTO cols
      FROM information_schema.columns
     WHERE table_schema = 'raw' AND table_name = 'application_test' AND column_name <> 'sk_id_curr';
    EXECUTE format($f$
        CREATE TABLE staging.application AS
        SELECT 'train'::text AS source, t.sk_id_curr, t.target, %1$s FROM raw.application_train t
        UNION ALL
        SELECT 'test'::text  AS source, t.sk_id_curr, NULL::smallint, %1$s FROM raw.application_test t
    $f$, cols);
END $$;

UPDATE staging.application SET code_gender = NULL        WHERE code_gender = 'XNA';
UPDATE staging.application SET name_family_status = NULL WHERE name_family_status = 'Unknown';

ALTER TABLE staging.application ADD COLUMN days_employed_anom smallint;
ALTER TABLE staging.application ADD COLUMN days_employed_clean double precision;
UPDATE staging.application
   SET days_employed_anom  = (days_employed = {{days_employed_sentinel}})::int,
       days_employed_clean = NULLIF(days_employed, {{days_employed_sentinel}});

ALTER TABLE staging.application ADD PRIMARY KEY (sk_id_curr);

-- =============================================================================
-- staging.application_derived — grain: 1 row per SK_ID_CURR. Spec §10.2 ratio features.
-- All divisions are protected with NULLIF(denominator, 0).
-- =============================================================================
DROP TABLE IF EXISTS staging.application_derived CASCADE;
CREATE TABLE staging.application_derived AS
SELECT
    a.sk_id_curr,
    -a.days_birth / 365.25                                         AS age_years,
    -a.days_employed_clean / 365.25                                AS employment_years,
    (-a.days_employed_clean) / NULLIF(-a.days_birth::double precision, 0) AS employment_age_ratio,
    a.amt_credit / NULLIF(a.amt_income_total, 0)                   AS credit_income_ratio,
    a.amt_annuity / NULLIF(a.amt_income_total, 0)                  AS annuity_income_ratio,
    a.amt_credit / NULLIF(a.amt_annuity, 0)                        AS credit_annuity_ratio,
    a.amt_goods_price / NULLIF(a.amt_credit, 0)                    AS goods_credit_ratio,
    a.amt_credit - a.amt_goods_price                               AS credit_minus_goods,
    a.amt_income_total / NULLIF(a.cnt_fam_members, 0)              AS income_per_person,
    -a.days_id_publish / 365.25                                    AS days_id_publish_years,
    -a.days_registration / 365.25                                  AS days_registration_years,
    -a.days_last_phone_change / 365.25                             AS phone_change_years,
    ( COALESCE(a.flag_document_2,0)  + COALESCE(a.flag_document_3,0)  + COALESCE(a.flag_document_4,0)
    + COALESCE(a.flag_document_5,0)  + COALESCE(a.flag_document_6,0)  + COALESCE(a.flag_document_7,0)
    + COALESCE(a.flag_document_8,0)  + COALESCE(a.flag_document_9,0)  + COALESCE(a.flag_document_10,0)
    + COALESCE(a.flag_document_11,0) + COALESCE(a.flag_document_12,0) + COALESCE(a.flag_document_13,0)
    + COALESCE(a.flag_document_14,0) + COALESCE(a.flag_document_15,0) + COALESCE(a.flag_document_16,0)
    + COALESCE(a.flag_document_17,0) + COALESCE(a.flag_document_18,0) + COALESCE(a.flag_document_19,0)
    + COALESCE(a.flag_document_20,0) + COALESCE(a.flag_document_21,0) )   AS document_count,
    ( COALESCE(a.flag_mobil,0) + COALESCE(a.flag_emp_phone,0) + COALESCE(a.flag_work_phone,0)
    + COALESCE(a.flag_cont_mobile,0) + COALESCE(a.flag_phone,0) + COALESCE(a.flag_email,0) ) AS contactability_count,
    ext.ext_source_mean,
    ext.ext_source_min,
    ext.ext_source_max,
    ext.ext_source_std,
    ext.ext_source_missing_count,
    -- missingness can itself be informative (kept separately from any model-side imputation)
    (a.ext_source_1 IS NULL)::int                                  AS ext_source_1_missing,
    (a.ext_source_3 IS NULL)::int                                  AS ext_source_3_missing,
    (a.amt_req_credit_bureau_year IS NULL)::int                    AS bureau_enquiry_missing
FROM staging.application a
CROSS JOIN LATERAL (
    SELECT avg(v)          AS ext_source_mean,
           min(v)          AS ext_source_min,
           max(v)          AS ext_source_max,
           stddev_pop(v)   AS ext_source_std,
           3 - count(v)    AS ext_source_missing_count
      FROM unnest(ARRAY[a.ext_source_1, a.ext_source_2, a.ext_source_3]) AS v
) ext;

ALTER TABLE staging.application_derived ADD PRIMARY KEY (sk_id_curr);
