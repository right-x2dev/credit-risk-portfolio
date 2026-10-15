-- =============================================================================
-- Model monitoring facts (Page 4). All keyed to dim_model_version and dim_split.
-- =============================================================================
DROP TABLE IF EXISTS mart.fact_model_evaluation CASCADE;
CREATE TABLE mart.fact_model_evaluation AS
SELECT v.model_version_key,
       e.model_name,
       e.score_type,
       e.split                                         AS dataset_split,
       e.metric,
       e.value,
       CASE e.metric WHEN 'roc_auc' THEN 'ROC-AUC' WHEN 'pr_auc' THEN 'PR-AUC' WHEN 'brier' THEN 'Brier Score'
                     WHEN 'log_loss' THEN 'Log Loss' WHEN 'ks' THEN 'KS'
                     WHEN 'calibration_in_the_large' THEN 'Calibration-in-the-large'
                     WHEN 'precision_at_threshold' THEN 'Precision @ threshold'
                     WHEN 'recall_at_threshold' THEN 'Recall @ threshold'
                     WHEN 'f1_at_threshold' THEN 'F1 @ threshold'
                     WHEN 'top_decile_lift' THEN 'Top-decile lift'
                     ELSE e.metric END                 AS metric_label,
       (e.model_name = v.algorithm AND e.score_type = 'calibrated') AS is_champion_row
  FROM analytics.model_metrics e
  JOIN mart.dim_model_version v USING (model_version);

DROP TABLE IF EXISTS mart.fact_model_curve CASCADE;
CREATE TABLE mart.fact_model_curve AS
SELECT v.model_version_key, c.split AS dataset_split, c.curve, c.x, c.y, c.threshold,
       row_number() OVER (PARTITION BY c.model_version, c.split, c.curve ORDER BY c.x) AS point_order
  FROM analytics.model_curves c
  JOIN mart.dim_model_version v USING (model_version);

DROP TABLE IF EXISTS mart.fact_calibration CASCADE;
CREATE TABLE mart.fact_calibration AS
SELECT v.model_version_key, c.split AS dataset_split, c.score_type, c.bin AS pd_bin, c.customers, c.positives,
       c.mean_pd, c.pd_lower, c.pd_upper, c.observed_rate, c.ci_low, c.ci_high, c.exposure, c.small_sample
  FROM analytics.calibration_table c
  JOIN mart.dim_model_version v USING (model_version);

DROP TABLE IF EXISTS mart.fact_lift CASCADE;
CREATE TABLE mart.fact_lift AS
SELECT v.model_version_key, l.split AS dataset_split, l.decile, l.customers, l.positives, l.mean_pd, l.min_pd,
       l.max_pd, l.exposure, l.bad_exposure, l.observed_rate, l.lift, l.cum_capture_rate, l.cum_customer_share,
       l.cum_bad_exposure_capture, l.exposure_share
  FROM analytics.lift_table l
  JOIN mart.dim_model_version v USING (model_version);

DROP TABLE IF EXISTS mart.fact_feature_importance CASCADE;
CREATE TABLE mart.fact_feature_importance AS
SELECT v.model_version_key, f.feature, f.rank AS importance_rank, f.mean_abs_shap, f.gain_importance,
       f.split_importance, f.direction
  FROM analytics.feature_importance f
  JOIN mart.dim_model_version v USING (model_version);

DROP TABLE IF EXISTS mart.fact_segment_performance CASCADE;
CREATE TABLE mart.fact_segment_performance AS
SELECT v.model_version_key, s.split AS dataset_split, s.group_attribute, s.group_value, s.customers, s.positives,
       s.observed_rate, s.mean_pd, s.roc_auc, s.recall_at_threshold, s.precision_at_threshold, s.flagged_share,
       s.exposure, s.small_sample_warning
  FROM analytics.segment_performance s
  JOIN mart.dim_model_version v USING (model_version);

-- 2x2 confusion matrix at the frozen threshold (calibrated champion), long format for a matrix visual
DROP TABLE IF EXISTS mart.fact_confusion_matrix CASCADE;
CREATE TABLE mart.fact_confusion_matrix AS
WITH m AS (
    SELECT model_version_key, dataset_split,
           max(value) FILTER (WHERE metric = 'tp_at_threshold') AS tp,
           max(value) FILTER (WHERE metric = 'fp_at_threshold') AS fp,
           max(value) FILTER (WHERE metric = 'tn_at_threshold') AS tn,
           max(value) FILTER (WHERE metric = 'fn_at_threshold') AS fn
      FROM mart.fact_model_evaluation
     WHERE is_champion_row
     GROUP BY model_version_key, dataset_split
)
SELECT model_version_key, dataset_split, 'Actual 1 (difficulty)' AS actual, 1 AS actual_sort,
       'Flagged (PD >= thr)' AS predicted, 1 AS predicted_sort, tp AS customers FROM m
UNION ALL SELECT model_version_key, dataset_split, 'Actual 1 (difficulty)', 1, 'Not flagged', 2, fn FROM m
UNION ALL SELECT model_version_key, dataset_split, 'Actual 0', 2, 'Flagged (PD >= thr)', 1, fp FROM m
UNION ALL SELECT model_version_key, dataset_split, 'Actual 0', 2, 'Not flagged', 2, tn FROM m;
