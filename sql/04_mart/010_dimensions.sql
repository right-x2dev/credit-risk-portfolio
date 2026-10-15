-- =============================================================================
-- Power BI star schema — dimensions (spec §10.8). Dimension -> fact, single direction, 1:N.
-- =============================================================================

-- dim_risk_grade: grade boundaries come from configs/project.yaml via analytics.cfg_risk_grade
DROP TABLE IF EXISTS mart.dim_risk_grade CASCADE;
CREATE TABLE mart.dim_risk_grade AS
SELECT sort_key::int        AS risk_grade_key,
       grade                AS risk_grade,
       risk_group,
       label                AS risk_grade_label,
       pd_lower,
       LEAST(pd_upper, 1.0) AS pd_upper,
       sort_key::int        AS sort_key,
       color_hex,
       is_high_risk
  FROM analytics.cfg_risk_grade;
ALTER TABLE mart.dim_risk_grade ADD PRIMARY KEY (risk_grade_key);

-- dim_model_version
DROP TABLE IF EXISTS mart.dim_model_version CASCADE;
CREATE TABLE mart.dim_model_version AS
SELECT row_number() OVER (ORDER BY trained_at, model_version)::int AS model_version_key,
       model_version,
       algorithm,
       trained_at,
       trained_at::date                                     AS trained_date,
       feature_version,
       feature_count,
       calibration_method,
       selected_threshold,
       split_seed,
       is_champion,
       left(feature_schema_hash, 12)                        AS feature_schema_hash
  FROM audit.model_registry;
ALTER TABLE mart.dim_model_version ADD PRIMARY KEY (model_version_key);

-- dim_split: lets Power BI default to the unbiased holdout evaluation
DROP TABLE IF EXISTS mart.dim_split CASCADE;
CREATE TABLE mart.dim_split (
    dataset_split   text PRIMARY KEY,
    split_label     text NOT NULL,
    sort_key        int  NOT NULL,
    is_evaluation   boolean NOT NULL,
    description     text NOT NULL
);
INSERT INTO mart.dim_split VALUES
  ('holdout',    'Holdout (evaluation)',   1, true,  'Unseen 15% of labelled customers; used once for final evaluation'),
  ('validation', 'Validation',             2, true,  '15% used for model selection, calibration and threshold'),
  ('train',      'Train (in-sample)',      3, false, '70% used for fitting; in-sample scores are optimistic');

-- dim_customer_segment: one row per distinct combination of segment attributes
-- Bands use config tables (analytics.cfg_age_band / cfg_income_band), not hard-coded thresholds.
DROP TABLE IF EXISTS staging.customer_segment_attr CASCADE;
CREATE TABLE staging.customer_segment_attr AS
SELECT m.sk_id_curr,
       COALESCE(ab.band, 'Unknown')                                         AS age_band,
       COALESCE(ab.sort_key, 99)                                            AS age_band_sort,
       COALESCE(ib.band, 'Unknown')                                         AS income_band,
       COALESCE(ib.sort_key, 99)                                            AS income_band_sort,
       CASE m.code_gender WHEN 'F' THEN 'Female' WHEN 'M' THEN 'Male' ELSE 'Unknown' END AS gender,
       COALESCE(m.name_family_status, 'Unknown')                            AS family_status,
       COALESCE(m.name_housing_type, 'Unknown')                             AS housing_type,
       COALESCE(m.name_education_type, 'Unknown')                           AS education,
       COALESCE(m.name_income_type, 'Unknown')                              AS income_type,
       COALESCE(m.occupation_type, 'Not stated')                            AS occupation,
       COALESCE(m.name_contract_type, 'Unknown')                            AS contract_type,
       CASE WHEN m.cnt_children IS NULL THEN 'Unknown'
            WHEN m.cnt_children = 0 THEN 'No children'
            WHEN m.cnt_children = 1 THEN '1 child'
            ELSE '2+ children' END                                          AS children_group,
       'Region rating ' || COALESCE(m.region_rating_client::int::text, '?') AS region_rating
  FROM analytics.customer_feature_mart m
  LEFT JOIN analytics.cfg_age_band ab
         ON m.age_years >= ab.lower AND m.age_years < ab.upper
  LEFT JOIN analytics.cfg_income_band ib
         ON m.amt_income_total >= ib.lower AND m.amt_income_total < ib.upper
 WHERE m.source = 'train';

DROP TABLE IF EXISTS mart.dim_customer_segment CASCADE;
CREATE TABLE mart.dim_customer_segment AS
SELECT row_number() OVER (ORDER BY age_band_sort, income_band_sort, gender, family_status, housing_type, education,
                                   income_type, occupation, contract_type, children_group, region_rating)::int
                                                                         AS segment_key,
       s.*
  FROM (SELECT DISTINCT age_band, age_band_sort, income_band, income_band_sort, gender, family_status, housing_type,
                        education, income_type, occupation, contract_type, children_group, region_rating
          FROM staging.customer_segment_attr) s;
ALTER TABLE mart.dim_customer_segment ADD PRIMARY KEY (segment_key);
