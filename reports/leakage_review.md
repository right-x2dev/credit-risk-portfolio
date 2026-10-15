# Leakage Review

> ⚠️ **합성(synthetic) 테스트 데이터 기반 결과 — 실제 Home Credit 분석 결과 아님.** 실제 Kaggle 데이터를 `data/raw/`에 넣고 `./scripts/run_all.ps1`을 실행하면 이 문서가 재생성됩니다.

_검토 기준: 명세서 §3.3. 자동 검사 결과 표는 `credit-risk docs` 실행 시 갱신된다._

## 1. 결론
- 모델 입력은 **현재 신청의 의사결정 시점 이전에 기록된 정보**로 제한했다. 시점 확인이 불가능한 사후 정보는 제거하거나 NULL 처리했다.
- `TARGET` 및 그 파생값은 이름·패턴 규칙으로 Feature 목록에서 차단되고, 단일 Feature AUC 상한 검사로 재확인된다
  (최근 실행: 최대 단일 Feature AUC = **0.7154**, `ext_source_mean`; 상한 0.95).
- 전처리는 split 이후 Train에만 fit하고, 교정·threshold는 Validation에서 정하며, Holdout은 최종 1회 평가에만 사용한다.

## 2. 원천별 시점 검토
| 원천 | 시점 기준 | 처리 | 위험 |
|---|---|---|---|
| application_train/test | 신청서 작성 시점 | 그대로 사용, `DAYS_EMPLOYED=365243` sentinel 정제 | 낮음 |
| application `*_SOCIAL_CIRCLE`, `DAYS_LAST_PHONE_CHANGE`, `AMT_REQ_CREDIT_BUREAU_*` | 신청 시점 기록으로 문서화되었으나 산출 시점 정의가 불명확 | 기본 Feature에 포함, `sensitivity_review` 목록으로 별도 추적 | 중간 |
| bureau | `DAYS_CREDIT` 등 신청일 기준 상대일 | `DAYS_CREDIT > 0`, `DAYS_ENDDATE_FACT > 0`, `DAYS_CREDIT_UPDATE > 0` 레코드 제외 | 낮음 |
| bureau_balance | `MONTHS_BALANCE` 신청월 기준 | 신청월(0) 제외, `< 0`만 사용 | 낮음 |
| previous_application | `DAYS_DECISION` | `> 0` 제외, 실제 종료일(`DAYS_LAST_DUE`, `DAYS_TERMINATION`)이 양수이면 NULL | 낮음 |
| POS_CASH / credit_card | `MONTHS_BALANCE` | `< 0`만 사용 | 낮음 |
| installments_payments | `DAYS_INSTALMENT`, `DAYS_ENTRY_PAYMENT` | 둘 다 `<= 0`인 행만 사용 | 낮음 |
| 과거 신청 결과(`prev_last_status`, 거절 횟수) | 과거 신청에 대한 과거 결정 | 사용 — 현재 신청의 결과가 아님 | 낮음 |

> 연체·상환 지표(Power BI Page 3)는 모두 **현재 신청 이전** 이력이다. 현재 대출의 사후 상환 정보는 데이터에 없고 사용하지 않는다.

## 3. 제외 Feature (`configs/features.yaml`)
- 명시 제외: `sk_id_curr`, `source`, `feature_version`, `built_at`, `target`, `days_employed`
- 패턴 제외: `*target*`, `*predicted_pd*`, `*risk_grade*`, `*expected_loss*`
- 민감도 검토 대상(포함하되 추적): `def_30_cnt_social_circle`, `def_60_cnt_social_circle`, `obs_30_cnt_social_circle`, `obs_60_cnt_social_circle`, `days_last_phone_change`, `amt_req_credit_bureau_hour`, `amt_req_credit_bureau_day`, `amt_req_credit_bureau_week`

## 4. 분할·전처리 누수 통제
- `analytics.model_split`: stratified 70/15/15, seed 고정, 고객 ID 교집합 0 자동 검사(`split_id_intersection`).
- `application_test`는 내부 holdout으로 사용하지 않는다(무라벨 scoring 예시).
- LightGBM early stopping은 Train 내부 slice만 사용한다. Target encoding은 사용하지 않는다.
- 교정 방법 비교는 Validation 내부 CV, 최종 calibrator는 Validation 전체로 fit한다. Holdout에 fit하지 않는다.

## 5. 자동 검사 결과 (최근 실행)
| Layer | Check | Table | Column | Status | Observed | Rule |
|---|---|---|---|---|---:|---|
| analytics | label_absent_in_test | analytics.customer_feature_mart | target | pass | 0 | application_test rows carry no TARGET |
| raw | post_application_record | raw.bureau | days_credit | pass | 0 | days_credit <= 0 (history must precede the current application) |
| raw | post_application_record | raw.bureau_balance | months_balance | pass | 0 | months_balance <= 0 (history must precede the current application) |
| raw | post_application_record | raw.credit_card_balance | months_balance | pass | 0 | months_balance <= 0 (history must precede the current application) |
| raw | post_application_record | raw.installments_payments | days_instalment | pass | 0 | days_instalment <= 0 (history must precede the current application) |
| raw | post_application_record | raw.pos_cash_balance | months_balance | pass | 0 | months_balance <= 0 (history must precede the current application) |
| raw | post_application_record | raw.previous_application | days_decision | pass | 0 | days_decision <= 0 (history must precede the current application) |
| raw | train_test_id_overlap | raw.application_train | sk_id_curr | pass | 0 | train ∩ test SK_ID_CURR = 0 |
| staging | leakage_filter_removed_rows | staging.bureau | nan | pass | 90 | records dated after the application are excluded (warn if > 1%) |
| staging | leakage_filter_removed_rows | staging.previous_application | nan | pass | 0 | records dated after the application are excluded (warn if > 1%) |

## 6. 남은 위험
- Kaggle 데이터에는 실제 calendar date가 없어 out-of-time 검증이 불가능하다.
- `EXT_SOURCE_*`는 생성 시점·방법이 공개되지 않은 외부 점수다. 신청 시점 제공 정보로 간주했으나, 외부 점수가 유사 레이블을 학습했을 가능성은 배제할 수 없다.
- Power BI에 노출되는 Train/Validation 예측은 in-sample 또는 선택용이며, 성능 판단은 Holdout 기준으로만 한다.
