# DAX Measures (`00_Measures`)

_자동 생성: `credit-risk build-pbip`. 모델과 동일한 정의에서 생성되므로 문서와 PBIP가 항상 일치한다._

업무 규칙(Exposure proxy, LGD, EL, Risk Grade, 연체 구간)은 SQL mart에서 정의되고, DAX는 집계와 What-if 배율만 담당한다. What-if 값은 **상대 배율**이다(+20% = ×1.2, percentage point 아님).

## Metadata

### Header Info
- Format: `text`
```DAX
Header Info =
IF(SELECTEDVALUE(report_metadata[is_synthetic], FALSE()), "SYNTHETIC TEST DATA (not Home Credit) · ", "") & "Population " & FORMAT(MAX(report_metadata[population_customers]), "#,0") & " applications · Model " & MAX(report_metadata[model_version]) & " · Refreshed " & FORMAT(MAX(report_metadata[last_refreshed]), "yyyy-mm-dd hh:nn") & " UTC"
```

### Grade Color
- Format: `text`
```DAX
Grade Color =
SELECTEDVALUE(dim_risk_grade[color_hex], "#5B6B7F")
```

## Model

### ROC-AUC
- Format: `0.000`
```DAX
ROC-AUC =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "roc_auc", fact_model_evaluation[is_champion_row] = TRUE()))
```

### PR-AUC
- Format: `0.000`
```DAX
PR-AUC =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "pr_auc", fact_model_evaluation[is_champion_row] = TRUE()))
```

### Brier Score
- Format: `0.0000`
```DAX
Brier Score =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "brier", fact_model_evaluation[is_champion_row] = TRUE()))
```

### Log Loss
- Format: `0.0000`
```DAX
Log Loss =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "log_loss", fact_model_evaluation[is_champion_row] = TRUE()))
```

### KS
- Format: `0.000`
```DAX
KS =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "ks", fact_model_evaluation[is_champion_row] = TRUE()))
```

### Recall at Threshold
- Format: `0.0%`
```DAX
Recall at Threshold =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "recall_at_threshold", fact_model_evaluation[is_champion_row] = TRUE()))
```

### Precision at Threshold
- Format: `0.0%`
```DAX
Precision at Threshold =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "precision_at_threshold", fact_model_evaluation[is_champion_row] = TRUE()))
```

### Calibration in the Large
- Format: `+0.00%;-0.00%`
```DAX
Calibration in the Large =
IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX(fact_model_evaluation[value]), fact_model_evaluation[metric] = "calibration_in_the_large", fact_model_evaluation[is_champion_row] = TRUE()))
```

### Selected Threshold
- Format: `0.00%`
```DAX
Selected Threshold =
MAX(dim_model_version[selected_threshold])
```

### ROC True Positive Rate
- Format: `0.000`
```DAX
ROC True Positive Rate =
CALCULATE(AVERAGE(fact_model_curve[y]), fact_model_curve[curve] = "roc")
```

### Random Classifier
- Format: `0.000`
```DAX
Random Classifier =
CALCULATE(AVERAGE(fact_model_curve[x]), fact_model_curve[curve] = "roc")
```

### PR Precision
- Format: `0.000`
```DAX
PR Precision =
CALCULATE(AVERAGE(fact_model_curve[y]), fact_model_curve[curve] = "pr")
```

### Calibration Mean PD
- Format: `0.00%`
```DAX
Calibration Mean PD =
CALCULATE(AVERAGE(fact_calibration[mean_pd]), fact_calibration[score_type] = "calibrated")
```

### Calibration Observed Rate
- Format: `0.00%`
```DAX
Calibration Observed Rate =
CALCULATE(AVERAGE(fact_calibration[observed_rate]), fact_calibration[score_type] = "calibrated")
```

### Raw Score Mean PD
- Format: `0.00%`
```DAX
Raw Score Mean PD =
CALCULATE(AVERAGE(fact_calibration[mean_pd]), fact_calibration[score_type] = "raw")
```

### Calibration Bin Customers
- Format: `#,0`
```DAX
Calibration Bin Customers =
CALCULATE(SUM(fact_calibration[customers]), fact_calibration[score_type] = "calibrated")
```

### Confusion Count
- Format: `#,0`
```DAX
Confusion Count =
SUM(fact_confusion_matrix[customers])
```

### Decile Lift
- Format: `0.00`
```DAX
Decile Lift =
AVERAGE(fact_lift[lift])
```

### Cumulative Capture
- Format: `0.0%`
```DAX
Cumulative Capture =
AVERAGE(fact_lift[cum_capture_rate])
```

### Cumulative Exposure Capture
- Format: `0.0%`
```DAX
Cumulative Exposure Capture =
AVERAGE(fact_lift[cum_bad_exposure_capture])
```

### Top 15 Mean abs SHAP
- Format: `0.0000`
```DAX
Top 15 Mean abs SHAP =
IF(MAX(fact_feature_importance[importance_rank]) <= 15, SUM(fact_feature_importance[mean_abs_shap]))
```

## Narrative

### Executive Narrative
- Format: `text`
```DAX
Executive Narrative =
"High/Critical grades hold " & FORMAT([High Risk Customer %], "0.0%") & " of customers but " & FORMAT([High Risk Exposure %], "0.0%") & " of exposure and " & FORMAT(DIVIDE([High Risk Expected Loss], [Base Expected Loss]), "0.0%") & " of expected loss (scenario). Exposure-weighted PD is " & FORMAT([Exposure Weighted PD], "0.00%") & " vs. an observed difficulty rate of " & FORMAT([Observed Difficulty Rate], "0.00%") & "."
```

### Model Summary Narrative
- Format: `text`
```DAX
Model Summary Narrative =
SELECTEDVALUE(dim_split[split_label], "Select one split") & ": AUC " & FORMAT([ROC-AUC], "0.000") & " · PR-AUC " & FORMAT([PR-AUC], "0.000") & " · KS " & FORMAT([KS], "0.000") & " · mean PD − observed " & FORMAT([Calibration in the Large], "+0.00%;-0.00%") & " · @thr " & FORMAT([Selected Threshold], "0.0%") & " recall " & FORMAT([Recall at Threshold], "0.0%") & ", precision " & FORMAT([Precision at Threshold], "0.0%") & " (proxy PD; associations, not causes)"
```

## Portfolio

### Total Customers
- Format: `#,0`
```DAX
Total Customers =
DISTINCTCOUNT(fact_customer_risk[sk_id_curr])
```

### Total Exposure
- Format: `#,0` · Exposure proxy = AMT_CREDIT (not an observed EAD)
```DAX
Total Exposure =
SUM(fact_customer_risk[exposure_proxy])
```

### Observed Difficulty Customers
- Format: `#,0`
```DAX
Observed Difficulty Customers =
CALCULATE([Total Customers], fact_customer_risk[target] = 1)
```

### Observed Difficulty Rate
- Format: `0.00%` · Share of customers with TARGET = 1 (observed repayment difficulty, not a lifetime default rate)
```DAX
Observed Difficulty Rate =
DIVIDE([Observed Difficulty Customers], [Total Customers])
```

### Average PD
- Format: `0.00%` · Simple average of calibrated proxy PD (predicts Home Credit TARGET)
```DAX
Average PD =
AVERAGE(fact_customer_risk[predicted_pd])
```

### Exposure Weighted PD
- Format: `0.00%`
```DAX
Exposure Weighted PD =
DIVIDE(SUMX(fact_customer_risk, fact_customer_risk[predicted_pd] * fact_customer_risk[exposure_proxy]), [Total Exposure])
```

### Base Expected Loss
- Format: `#,0` · Expected Loss (Scenario) = PD x AMT_CREDIT x assumed LGD; not a provision or regulatory figure
```DAX
Base Expected Loss =
SUM(fact_customer_risk[expected_loss])
```

### EL Rate
- Format: `0.00%`
```DAX
EL Rate =
DIVIDE([Base Expected Loss], [Total Exposure])
```

### High Risk Customers
- Format: `#,0`
```DAX
High Risk Customers =
CALCULATE([Total Customers], KEEPFILTERS(dim_risk_grade[risk_group] IN {"High", "Critical"}))
```

### High Risk Customer %
- Format: `0.0%`
```DAX
High Risk Customer % =
DIVIDE([High Risk Customers], [Total Customers])
```

### High Risk Exposure
- Format: `#,0`
```DAX
High Risk Exposure =
CALCULATE([Total Exposure], KEEPFILTERS(dim_risk_grade[risk_group] IN {"High", "Critical"}))
```

### High Risk Exposure %
- Format: `0.0%`
```DAX
High Risk Exposure % =
DIVIDE([High Risk Exposure], [Total Exposure])
```

### High Risk Expected Loss
- Format: `#,0`
```DAX
High Risk Expected Loss =
CALCULATE([Base Expected Loss], KEEPFILTERS(dim_risk_grade[risk_group] IN {"High", "Critical"}))
```

### Risk Capture (Exposure)
- Format: `0.0%` · Share of TARGET=1 exposure that sits in High/Critical grades
```DAX
Risk Capture (Exposure) =
DIVIDE(CALCULATE([Total Exposure], KEEPFILTERS(dim_risk_grade[risk_group] IN {"High", "Critical"}), fact_customer_risk[target] = 1), CALCULATE([Total Exposure], fact_customer_risk[target] = 1))
```

### Customer Share %
- Format: `0.0%`
```DAX
Customer Share % =
DIVIDE([Total Customers], CALCULATE([Total Customers], ALLSELECTED(dim_risk_grade)))
```

### Exposure Share %
- Format: `0.0%`
```DAX
Exposure Share % =
DIVIDE([Total Exposure], CALCULATE([Total Exposure], ALLSELECTED(dim_risk_grade)))
```

### EL Share of Portfolio %
- Format: `0.0%`
```DAX
EL Share of Portfolio % =
DIVIDE([Base Expected Loss], CALCULATE([Base Expected Loss], ALLSELECTED(fact_customer_risk)))
```

### Exposure Share of Portfolio %
- Format: `0.0%`
```DAX
Exposure Share of Portfolio % =
DIVIDE([Total Exposure], CALCULATE([Total Exposure], ALLSELECTED(fact_customer_risk)))
```

### Average Exposure
- Format: `#,0`
```DAX
Average Exposure =
DIVIDE([Total Exposure], [Total Customers])
```

## Repayment

### Past Due Customer %
- Format: `0.0%` · Customers with >= 1 late Home Credit instalment before this application (customers with history only)
```DAX
Past Due Customer % =
AVERAGE(fact_customer_risk[is_past_due_history])
```

### Severe Delinquency Customer %
- Format: `0.0%` · Max instalment DPD >= 30 days before this application
```DAX
Severe Delinquency Customer % =
AVERAGE(fact_customer_risk[is_severe_delinquency])
```

### Average Max DPD
- Format: `0.0`
```DAX
Average Max DPD =
AVERAGE(fact_customer_risk[inst_dpd_max])
```

### Median Max DPD
- Format: `0.0`
```DAX
Median Max DPD =
MEDIAN(fact_customer_risk[inst_dpd_max])
```

### Average Payment Ratio
- Format: `0.0%`
```DAX
Average Payment Ratio =
AVERAGE(fact_customer_risk[inst_payment_ratio_mean])
```

### Average Credit Utilization
- Format: `0.0%`
```DAX
Average Credit Utilization =
AVERAGE(fact_customer_risk[cc_utilization_mean])
```

## Scenario

### PD Stress Multiplier
- Format: `0.00x` · Relative multiplier: +20% means PD x 1.2 (not +20 percentage points)
```DAX
PD Stress Multiplier =
1 + SELECTEDVALUE('PD Stress'[PD Stress Value], 0)
```

### Selected LGD
- Format: `0.0%` · Assumed LGD (no observed recoveries in the data)
```DAX
Selected LGD =
SELECTEDVALUE('LGD Assumption'[LGD Assumption Value], 0.45)
```

### Exposure Multiplier
- Format: `0.00x`
```DAX
Exposure Multiplier =
1 + SELECTEDVALUE('Exposure Change'[Exposure Change Value], 0)
```

### Scenario Expected Loss
- Format: `#,0`
```DAX
Scenario Expected Loss =
VAR s = [PD Stress Multiplier] VAR l = [Selected LGD] VAR x = [Exposure Multiplier] RETURN SUMX(fact_customer_risk, MIN(1, fact_customer_risk[predicted_pd] * s) * fact_customer_risk[exposure_proxy] * x * l)
```

### Scenario EL Change
- Format: `#,0`
```DAX
Scenario EL Change =
[Scenario Expected Loss] - [Base Expected Loss]
```

### Scenario EL Change %
- Format: `+0.0%;-0.0%;0.0%`
```DAX
Scenario EL Change % =
DIVIDE([Scenario EL Change], [Base Expected Loss])
```

### Scenario Exposure
- Format: `#,0`
```DAX
Scenario Exposure =
[Total Exposure] * [Exposure Multiplier]
```

### Scenario EL Rate
- Format: `0.00%`
```DAX
Scenario EL Rate =
DIVIDE([Scenario Expected Loss], [Scenario Exposure])
```

### Scenario EL Share %
- Format: `0.0%`
```DAX
Scenario EL Share % =
DIVIDE([Scenario Expected Loss], CALCULATE([Scenario Expected Loss], ALLSELECTED(fact_customer_risk)))
```

### EL Bridge Value
- Format: `#,0`
```DAX
EL Bridge Value =
VAR s = [PD Stress Multiplier] VAR l = [Selected LGD] VAR x = [Exposure Multiplier] VAR el0 = [Base Expected Loss] VAR el1 = SUMX(fact_customer_risk, MIN(1, fact_customer_risk[predicted_pd] * s) * fact_customer_risk[exposure_proxy] * fact_customer_risk[base_lgd]) VAR el2 = SUMX(fact_customer_risk, MIN(1, fact_customer_risk[predicted_pd] * s) * fact_customer_risk[exposure_proxy] * l) VAR el3 = el2 * x RETURN SWITCH(SELECTEDVALUE('EL Bridge'[Step Order]), 1, el0, 2, el1 - el0, 3, el2 - el1, 4, el3 - el2)
```

### Sensitivity EL
- Format: `#,0`
```DAX
Sensitivity EL =
VAR s = SELECTEDVALUE('Sensitivity PD'[PD Stress Level], 0) VAR l = SELECTEDVALUE('Sensitivity LGD'[LGD Level], 0.45) RETURN SUMX(fact_customer_risk, MIN(1, fact_customer_risk[predicted_pd] * (1 + s)) * fact_customer_risk[exposure_proxy] * l)
```
