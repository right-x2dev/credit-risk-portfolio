# Credit Risk & Loan Portfolio Analytics Platform

**PostgreSQL · SQL data mart · Python · LightGBM · Power BI** 기반의 end-to-end 여신 포트폴리오 위험 분석 플랫폼입니다.
Kaggle *Home Credit Default Risk* 의 8개 관계형 파일을 고객 단위로 통합하고, 검증·교정된 **proxy PD** 를 Exposure proxy 및
가정 LGD와 결합해 Power BI 5개 페이지에서 위험 집중도·연체 특성·세그먼트·모델 성능·손실 시나리오를 탐색할 수 있게 합니다.

> ⚠️ **현재 저장소의 보고서·수치·스크린샷은 합성(synthetic) 테스트 데이터로 생성되었습니다.** Kaggle 데이터는 계정 로그인과
> 대회 규칙 동의가 필요해 자동으로 받지 않았습니다. 실제 데이터를 `data/raw/`에 넣고 `./scripts/run_all.ps1`을 실행하면
> 모든 리포트·모델 카드·PBIP가 실제 결과로 다시 생성됩니다. 합성 데이터에서 나온 숫자는 Home Credit에 대한 결과가 아닙니다.

![Executive overview](powerbi/screenshots/p1_executive.png)

## 이 프로젝트가 답하는 질문
- 포트폴리오의 고객 수, 총 Exposure, Exposure 가중 PD, 관측 상환곤란율, Expected Loss는 얼마인가?
- 고위험 고객 **수**보다 고위험 **Exposure** 비중이 더 큰가?
- 어느 소득·직업·연령·가족·주거 세그먼트에 위험과 Exposure가 함께 집중되는가?
- 과거 연체, 카드 사용률, 과거 신청 거절 이력은 현재 위험과 어떤 관계인가?
- 모델은 어떤 고객을 고위험으로 판단하며, 판별력·교정 상태는 어떤가?
- PD·LGD·Exposure가 악화되면 Expected Loss가 얼마나 늘어나는가?

## 아키텍처
```text
Kaggle CSV (data/raw, Git 제외)
   │  COPY (client-side), 파일 지문 기반 재실행 skip, audit.table_load_log
   ▼
raw schema ── 타입·행수·키·결측·orphan·TARGET·sentinel 검사 → audit.data_quality_result
   ▼
staging schema ── sentinel 정제, 신청일 이후 레코드 제외, 2단계 관계 집계
   │   bureau_balance→bureau→고객, installments 납부행→회차→고객, previous/POS/card→고객
   ▼
analytics.customer_feature_mart (SK_ID_CURR 1행, PK 강제) ──► Python EDA·통계 (reports/)
   ▼
ML: 70/15/15 split → Baseline / Logistic / LightGBM (Pipeline, train-only fit)
    → Validation 선택 → Platt vs Isotonic 교정(Validation CV) → Holdout 1회 평가 → SHAP·reason codes
   ▼
analytics.model_predictions ──► mart schema: Star schema + KPI/대조 view
   ▼
Power BI PBIP (TMDL + PBIR, mart에서 자동 생성) — 5 pages, What-if 3종, DAX↔SQL 42/42 대조
```

| Schema | 역할 | Power BI 연결 |
|---|---|---|
| `raw` | CSV 원형 보존(컬럼명 소문자) | 금지 |
| `staging` | 정제·파생·중간 집계 | 금지 |
| `analytics` | Feature mart, 모델 입출력, 설정 테이블 | 제한 |
| `mart` | Star schema, KPI view | 허용 |
| `audit` | 실행 이력, 적재 로그, 품질 결과, 모델 레지스트리 | 선택 |

## 데이터셋
- 출처: [Kaggle — Home Credit Default Risk](https://www.kaggle.com/c/home-credit-default-risk/data). 대회 규칙에 따라 원천 파일은 재배포하지 않으며 Git에서 제외합니다.
- 필요 파일: `application_train/test`, `bureau`, `bureau_balance`, `previous_application`, `POS_CASH_balance`, `credit_card_balance`, `installments_payments`, `HomeCredit_columns_description` (파일명 대소문자 무관).
- 받는 방법: Kaggle 로그인 → 대회 규칙 동의 → *Download All* → zip을 `data/raw/`에 풀기.
  또는 Kaggle API 토큰이 있다면 `kaggle competitions download -c home-credit-default-risk -p data/raw`로 받을 수 있습니다.

### `TARGET`의 의미와 한계
데이터 설명에 따르면 `TARGET=1`은 표본 대출의 **초기 상환 회차들 중 일정 일수 이상 연체가 한 번 이상 관측된, 상환에 어려움이 있었던 고객**입니다. `TARGET=0`은 그 외 고객입니다.
따라서 이 프로젝트는 TARGET 비율을 **관측 상환곤란율(Observed Difficulty Rate)** 이라고 부릅니다. 장기 생애부도율, 법적 파산, IFRS 9 Stage 3와 같은 개념으로 취급하지 않습니다. 모델 출력 `PD`는 **이 TARGET을 예측한 proxy PD**입니다.

## 빠른 시작 (Windows PowerShell)
```powershell
Copy-Item .env.example .env            # 로컬 데모용 값. 공유 환경이면 비밀번호 변경
docker compose up -d postgres
./scripts/run_all.ps1                  # Kaggle 파일이 data/raw/에 있을 때
./scripts/run_all.ps1 -Synthetic       # Kaggle 파일 없이 합성 데이터로 전체 흐름 시연 (~3분)
```
`run_all.ps1`은 다음 스크립트를 순서대로 호출합니다. 각 스크립트는 재실행할 수 있고, 실패하면 non-zero exit code를 반환합니다.

| Script | 단계 |
|---|---|
| `scripts/bootstrap.ps1` | uv로 Python 3.12 venv 생성(`requirements.lock`), PostgreSQL 16.4 기동, 스키마 생성, 파일 인벤토리 |
| `scripts/ingest.ps1` | raw 적재(COPY) + raw 품질 검사 (`-Force`로 강제 재적재) |
| `scripts/build_marts.ps1` | staging 집계 → feature mart → EDA/통계 리포트 → split |
| `scripts/train.ps1` | 학습·교정·평가 → scoring → BI mart → CSV 스냅샷 → 문서 → PBIP 생성·검증 |
| `scripts/test.ps1` | ruff + pytest (unit / integration / data_quality / model) |
| `scripts/powerbi_check.ps1` | Power BI Desktop 필요: TMDL 파싱, PBIR 스키마, 배포·새로고침·DAX↔SQL 대조, 페이지 캡처 |

macOS/Linux에서는 `Makefile`을 사용하세요(`make run-all`). Docker만으로 실행하려면 `docker compose --profile pipeline run --rm pipeline`을 사용합니다.

## 폴더 구조
```text
configs/        project.yaml(원천·관계·임계치·risk grade·LGD) · model.yaml · features.yaml(허용/제외 Feature)
sql/            00_init · 02_staging · 03_analytics · 04_mart · 99_teardown(수동 전용)
src/credit_risk ingest · validate · transform · features · split · train · calibrate · evaluate · explain · score
                mart · powerbi(PBIP 생성기) · eda · stats · docs · reporting · synthetic · cli
notebooks/      01 data audit · 02 EDA/stats · 03 feature research · 04 model diagnostics (설명용, 로직은 src/)
tests/          unit · integration · model · data_quality
reports/        eda_report · statistical_analysis · data_dictionary · leakage_review · model_card
                powerbi_guide · powerbi_validation · figures/
powerbi/        Credit_Risk_Portfolio.pbip (+ .SemanticModel / .Report) · theme.json · data_model.md · dax_measures.md · screenshots/
scripts/        PowerShell 파이프라인 + Power BI 검증 도구
```

## 주요 설계 결정
- **누수 방지:** 신청 시점 이후 레코드는 staging에서 제거합니다(`DAYS_* > 0`, `bureau_balance` 신청월 0, 실제 종료일이 양수인 과거 계약). Feature 정책은 config로 고정하고, 이름 규칙과 단일 Feature AUC 상한(0.95)으로 자동 검사합니다. 모든 전처리는 sklearn Pipeline 안에서 Train에만 fit하고, LightGBM early stopping은 Train 내부 slice만 씁니다. 교정과 threshold는 Validation에서 정하고 Holdout은 1회만 사용합니다. → [`reports/leakage_review.md`](reports/leakage_review.md)
- **Grain 보장:** 모든 집계 테이블에 PK를 걸고, 고객 mart에도 PK를 걸어 grain 위반 시 파이프라인을 실패시킵니다. 조인 증폭률 1.0을 검사합니다. installments는 부분납부를 반영해 납부행 → 회차 → 고객 순서로 집계합니다.
- **단일 업무 규칙:** Exposure proxy, LGD, EL, Risk grade, 연체 구간은 SQL에서만 정의합니다. DAX는 집계와 What-if 배율만 맡습니다.
- **Power BI as code:** `build-pbip`가 live `mart` 스키마를 읽어 TMDL/PBIR을 생성합니다. Power BI Desktop의 `TmdlSerializer`, 공식 JSON Schema, 로컬 엔진에서 실행한 DAX↔SQL 대조로 검증합니다.

## 분석 인사이트
자동 선정된 5~10개 인사이트(효과크기·Exposure 집중도 기준, 근거 그림·SQL 경로 포함)는 [`reports/eda_report.md`](reports/eda_report.md)에, 통계 검정(Mann–Whitney + rank-biserial, Chi-square + Cramér's V, Wilson CI, BH 보정)은 [`reports/statistical_analysis.md`](reports/statistical_analysis.md)에 있습니다.
**현재 버전은 합성 데이터 기준이므로 인사이트 내용은 실제 데이터 실행 후에만 해석해야 합니다.**

## 모델 성능과 한계
[`reports/model_card.md`](reports/model_card.md)에 Validation/Holdout 지표(ROC-AUC, PR-AUC, Log Loss, Brier, KS, calibration-in-the-large, lift, Exposure-weighted capture), 교정 비교, threshold 근거, SHAP, 세그먼트 진단, 한계를 정리했습니다. 모든 수치는 학습 실행에서 자동 기입되며 손으로 쓴 숫자는 없습니다.
- 한계: TARGET·Exposure·LGD가 모두 proxy 또는 가정입니다. calendar date가 없어 out-of-time 검증을 할 수 없습니다. 승인 고객만 결과가 관측되므로 reject bias가 있을 수 있습니다. 이 모델은 실제 승인 자동화에 사용하지 않습니다.

## Expected Loss 가정
```text
EL_i (Scenario) = PD_i (calibrated proxy PD) × Exposure Proxy_i (AMT_CREDIT) × LGD Assumption (기본 45%)
```
보고서에서는 `Expected Loss (Scenario)`로 표기합니다. 실제 회수·담보 데이터가 없어 규제·회계 수치(IFRS 9 ECL, Basel 자본)로 해석하면 안 됩니다. What-if는 상대 배율입니다(+20% = ×1.2).

## Power BI 연결·새로고침
[`reports/powerbi_guide.md`](reports/powerbi_guide.md)를 참조하세요. PBIP를 Desktop에서 열고 새로 고침하면 됩니다. 기본 데이터 소스는 PostgreSQL Import이고, `DataSource=CSV` 파라미터로 바꾸면 자격 증명 없이 볼 수 있습니다. 데이터 모델은 [`powerbi/data_model.md`](powerbi/data_model.md), measure는 [`powerbi/dax_measures.md`](powerbi/dax_measures.md)에 있습니다.

## 테스트
```powershell
./scripts/test.ps1              # ruff + 전체 pytest (PostgreSQL 필요)
./scripts/test.ps1 -UnitOnly    # DB 없이 단위 테스트만
```
- unit: sentinel·분모 0·DPD·payment ratio·grade 경계·PD clipping·EL/시나리오·config·split·누수 가드·지표·교정기·schema mismatch, 통계 함수
- integration: 합성 fixture → raw → staging → mart → 학습 → scoring → BI mart 전체 흐름(별도 `_test` DB), 재실행 시 중복 없음, 실패 실행의 audit 기록
- model: split 교집합 0, 전처리 train-only fit, baseline 대비 개선, holdout 지표 재현, calibration 표본 합계, artifact 재로딩
- data_quality: 프로젝트 DB의 키·TARGET·grain·PD/EL 범위·참조 무결성·grade 합계

## 보안·윤리
- 원천 데이터, `.env`, 모델 바이너리, DB dump는 Git에서 제외합니다(`.gitignore`, `.dockerignore`).
- 성별·연령·가족상태는 진단용 기술통계로만 쓰고, 승인 근거로 권고하지 않습니다. 세그먼트 성능표는 차별의 증명이 아닙니다.
- 예측과 SHAP은 연관 설명이며 인과 설명이 아닙니다. 실제 금융 의사결정에는 법률·규제·공정성 검토와 독립 검증이 추가로 필요합니다.
- **교육·포트폴리오 목적의 비규제 프로젝트입니다.**
