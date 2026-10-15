# Power BI Data Model

_자동 생성: `credit-risk build-pbip` — 아래 내용은 PBIP 시맨틱 모델과 동일한 정의에서 만들어진다._

## Star schema

```text
dim_risk_grade ──1:N──► fact_customer_risk ◄──N:1── dim_customer_segment
                         ▲            ▲
dim_model_version ──1:N──┘            └──N:1── dim_split
dim_risk_grade / dim_customer_segment ──1:N──► fact_scenario_base
dim_model_version / dim_split ──1:N──► fact_model_evaluation · fact_model_curve · fact_calibration · fact_lift
                                      fact_segment_performance · fact_confusion_matrix (+ fact_feature_importance)
Disconnected: 00_Measures · PD Stress · LGD Assumption · Exposure Change · Sensitivity PD · Sensitivity LGD · EL Bridge · report_metadata
```

- 모든 관계: **dimension → fact 단방향, 1:N**, 양방향·다대다 없음. 숫자 key·sort 컬럼은 숨김, 요약 안 함.
- Import mode. 데이터 소스는 `DataSource` 파라미터로 선택: `PostgreSQL`(기본, `mart` 스키마) 또는 `CSV`(`credit-risk export-bi` 스냅샷). 자격 증명은 PBIX/PBIP에 저장되지 않는다.
- Power Query는 원천 선택 + 타입 지정 2단계만 수행(`Source`, `Typed columns`). 행 수준 규칙은 모두 SQL mart에 있다.

## Relationships

| From (many) | To (one) | Direction |
|---|---|---|
| `fact_customer_risk[risk_grade_key]` | `dim_risk_grade[risk_grade_key]` | single (dim → fact) |
| `fact_customer_risk[segment_key]` | `dim_customer_segment[segment_key]` | single (dim → fact) |
| `fact_customer_risk[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_customer_risk[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |
| `fact_scenario_base[risk_grade_key]` | `dim_risk_grade[risk_grade_key]` | single (dim → fact) |
| `fact_scenario_base[segment_key]` | `dim_customer_segment[segment_key]` | single (dim → fact) |
| `fact_model_evaluation[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_model_curve[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_calibration[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_lift[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_feature_importance[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_segment_performance[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_confusion_matrix[model_version_key]` | `dim_model_version[model_version_key]` | single (dim → fact) |
| `fact_model_evaluation[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |
| `fact_model_curve[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |
| `fact_calibration[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |
| `fact_lift[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |
| `fact_segment_performance[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |
| `fact_confusion_matrix[dataset_split]` | `dim_split[dataset_split]` | single (dim → fact) |

## Tables

| Table | Kind | Columns | Measures | Source |
|---|---|---:|---:|---|
| `dim_risk_grade` | dimension | 9 | 0 | `mart.dim_risk_grade` |
| `dim_model_version` | dimension | 12 | 0 | `mart.dim_model_version` |
| `dim_split` | dimension | 5 | 0 | `mart.dim_split` |
| `dim_customer_segment` | dimension | 14 | 0 | `mart.dim_customer_segment` |
| `fact_customer_risk` | fact | 63 | 0 | `mart.fact_customer_risk` |
| `fact_scenario_base` | fact | 8 | 0 | `mart.fact_scenario_base` |
| `fact_model_evaluation` | fact | 8 | 0 | `mart.fact_model_evaluation` |
| `fact_model_curve` | fact | 7 | 0 | `mart.fact_model_curve` |
| `fact_calibration` | fact | 14 | 0 | `mart.fact_calibration` |
| `fact_lift` | fact | 16 | 0 | `mart.fact_lift` |
| `fact_feature_importance` | fact | 7 | 0 | `mart.fact_feature_importance` |
| `fact_segment_performance` | fact | 14 | 0 | `mart.fact_segment_performance` |
| `fact_confusion_matrix` | fact | 7 | 0 | `mart.fact_confusion_matrix` |
| `report_metadata` | metadata | 10 | 0 | `mart.v_report_metadata` |
| `00_Measures` | measure table | 0 | 61 | — |
| `PD Stress` | calculated (What-if) | 1 | 0 | `GENERATESERIES(-0.2, 1, 0.05)` |
| `LGD Assumption` | calculated (What-if) | 1 | 0 | `GENERATESERIES(0.1, 0.9, 0.05)` |
| `Exposure Change` | calculated (What-if) | 1 | 0 | `GENERATESERIES(-0.3, 0.3, 0.05)` |
| `Sensitivity PD` | calculated (What-if) | 1 | 0 | `SELECTCOLUMNS(GENERATESERIES(-0.2, 1, 0.2), "PD Stress Level` |
| `Sensitivity LGD` | calculated (What-if) | 1 | 0 | `SELECTCOLUMNS(GENERATESERIES(0.1, 0.9, 0.1), "LGD Level", [V` |
| `EL Bridge` | calculated (What-if) | 2 | 0 | `DATATABLE("Step", STRING, "Step Order", INTEGER, {{"1 Base E` |

## What-if parameters (relative multipliers)

| Table[column] | Range | Default (no selection) | Meaning |
|---|---|---|---|
| `'PD Stress'[PD Stress Value]` | −20% … +100%, step 5% | 0% | PD × (1 + value), capped at 1 |
| `'LGD Assumption'[LGD Assumption Value]` | 10% … 90%, step 5% | 45% | replaces the base LGD assumption |
| `'Exposure Change'[Exposure Change Value]` | −30% … +30%, step 5% | 0% | exposure × (1 + value) |

+20%는 **×1.2 (상대 배율)** 이며 +20 percentage point가 아니다. 시나리오 measure는 `dax_measures.md` 참조.
