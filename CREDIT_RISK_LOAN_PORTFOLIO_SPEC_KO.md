# Credit Risk & Loan Portfolio Analytics Platform

> Home Credit 데이터를 활용한 PostgreSQL · SQL Data Mart · Python · Machine Learning · Power BI 중심 End-to-End 포트폴리오 프로젝트 명세서

---

## 0. 문서 정보

| 항목 | 내용 |
|---|---|
| 문서 목적 | Claude Code가 프로젝트를 단계별로 구현·검증·문서화할 수 있도록 하는 실행 명세 |
| 주 사용 언어 | 한국어 문서, 영문 코드·테이블명·컬럼명 |
| 핵심 산출물 | Power BI 보고서(`.pbix`)와 이를 안정적으로 공급하는 분석 데이터 마트 |
| 원천 데이터 | Kaggle Home Credit Default Risk 데이터셋 |
| 주요 기술 | PostgreSQL, SQL, Python, scikit-learn/LightGBM 또는 XGBoost, Power BI, DAX, Docker Compose |
| 프로젝트 성격 | 금융 여신 포트폴리오 모니터링 및 신용위험 의사결정 지원 |
| 비핵심 목표 | Kaggle 순위 경쟁, 과도한 모델 튜닝, 실시간 운영 시스템 구축 |
| 문서 버전 | v1.0 |

---

## 1. 프로젝트 한 줄 정의

대출 신청자·과거 대출·외부 신용정보·상환·카드 사용 데이터를 관계형으로 통합하고, 고객별 부도확률(PD)과 포트폴리오 Exposure 및 Expected Loss를 계산하여 **여신 리스크 담당자가 Power BI에서 위험 집중도, 연체 특성, 세그먼트, 시나리오를 탐색할 수 있게 하는 분석 플랫폼**을 구축한다.

---

## 2. 프로젝트 목표와 비목표

### 2.1 목표

1. Home Credit의 다중 관계형 파일을 PostgreSQL에 원형 보존 방식으로 적재한다.
2. 원천 테이블과 분석용 Feature Mart, BI용 Star Schema를 명확히 분리한다.
3. 데이터 품질, 결측, 이상치, 관계 무결성, 집계 단위(grain)를 검증한다.
4. 탐색적 분석과 통계 검정을 통해 위험요인과 포트폴리오 특성을 설명한다.
5. 데이터 누수를 방지한 검증 체계로 PD 모델을 개발하고 확률을 교정한다.
6. 신청 시점에서 알 수 있는 정보만으로 고객별 PD를 생성한다.
7. 명확한 가정 아래 Exposure, LGD, Expected Loss를 계산한다.
8. Power BI 5개 페이지에서 경영·리스크·심사 관점의 질문에 답한다.
9. What-if Parameter로 PD/LGD/Exposure 변화에 따른 손실 시나리오를 제공한다.
10. Docker, 자동화 스크립트, 테스트, 데이터 사전, 모델 카드, 실행 문서로 재현성을 확보한다.

### 2.2 비목표

- 실제 금융기관의 승인·거절 결정을 자동화하지 않는다.
- 데이터에 존재하지 않는 실제 EAD, 회수율, 담보가치, 만기구조를 사실처럼 주장하지 않는다.
- IFRS 9 또는 Basel 규제자본 산출 시스템을 구현했다고 표현하지 않는다.
- Home Credit의 `TARGET`을 장기 생애부도, 법적 파산, 회계상 Stage 3와 동일시하지 않는다.
- 테스트 데이터의 정답을 추정하거나 Kaggle 제출 점수만을 위해 피처를 만든다.
- 모델 정확도를 BI, 데이터 모델, 해석 가능성, 재현성보다 우선하지 않는다.

---

## 3. 핵심 원칙

### 3.1 Power BI가 최종 제품이다

이 프로젝트의 성공 기준은 “AUC가 높은 노트북”이 아니다. 최종 사용자가 다음 질문에 답할 수 있어야 한다.

- 현재 포트폴리오의 고객 수, 총 Exposure, 평균 PD, 관측 부도율, Expected Loss는 얼마인가?
- 고위험 고객 수보다 고위험 고객의 Exposure 비중이 더 큰가?
- 어느 소득·직업·연령·가족·주거·지역 세그먼트에 위험이 집중되는가?
- 과거 연체, 카드 사용률, 이전 신청 이력은 현재 위험과 어떤 관계가 있는가?
- 모델은 어떤 고객을 고위험으로 판단하며 그 이유는 무엇인가?
- PD, LGD 또는 Exposure가 악화되면 Expected Loss가 얼마나 증가하는가?

모든 Python 및 SQL 산출물은 이 질문에 답하는 Power BI 모델을 공급해야 한다.

### 3.2 `TARGET`의 정확한 의미

`application_train.csv`의 `TARGET`은 현재 신청 건에 대해 관측된 상환 곤란 여부를 나타내는 이진 레이블이다.

- `TARGET = 1`: 상환에 어려움이 있었던 고객. 데이터 설명상 대출 표본의 초기 기간 중 **X일 이상 연체한 사례가 한 번 이상 존재하는 등** 상환 곤란 사례로 분류된 대상이다. 정확한 운영 정의는 배포된 데이터 설명 원문을 README에 인용 없이 요약하고 출처 링크를 남긴다.
- `TARGET = 0`: 위 기준의 상환 곤란이 관측되지 않은 고객.
- `application_test.csv`에는 `TARGET`이 없다.

따라서 보고서에서는 `TARGET` 기반 비율을 다음처럼 명명한다.

- 권장: `Observed Difficulty Rate`, `관측 상환곤란율`, `Observed Default Proxy Rate`
- 비권장: 실제 평생 부도율, 파산율, IFRS 9 부도율

모델 출력은 `PD`라는 업무 친화적 명칭을 사용하되, 문서와 툴팁에 **Home Credit의 TARGET을 예측한 proxy PD**임을 명시한다.

### 3.3 데이터 누수 방지

모델 입력값은 원칙적으로 현재 신청의 의사결정 시점에 이용 가능한 정보만 사용한다.

절대 원칙:

1. `TARGET` 자체 또는 이를 직접·간접 인코딩한 컬럼은 Feature에 포함하지 않는다.
2. Train/Test 분할 전 전체 데이터로 결측 대치, 스케일링, 인코딩, 변수 선택을 학습하지 않는다.
3. 모든 전처리는 `Pipeline`/`ColumnTransformer` 안에서 Train fold에만 fit한다.
4. Target encoding을 사용할 경우 fold 내부 OOF 방식만 허용한다.
5. 모델 선택은 validation 또는 cross-validation으로 수행하고 holdout test는 최종 1회 평가에 사용한다.
6. 동일 `SK_ID_CURR`이 서로 다른 split에 중복되지 않도록 검증한다.
7. 보조 테이블은 `SK_ID_CURR` 단위로 집계한 뒤 application 테이블에 조인하여 1고객 1행을 보장한다.
8. 현재 신청 이후에 생성된 정보인지 판단할 수 없는 컬럼은 데이터 사전에 위험 표시하고 기본 Feature Set에서 제외하거나 민감도 분석으로 분리한다.
9. 신청 결과 또는 사후 프로세스를 대변할 가능성이 있는 컬럼은 leakage review 목록에 기록한다.
10. Power BI에 holdout 실제 레이블과 예측값을 노출할 때는 평가용 데이터임을 명확히 표시한다.

### 3.4 가정과 사실 분리

- 원천 데이터 사실, 계산 결과, 모델 예측, 시나리오 가정을 시각적으로 구분한다.
- 실제 LGD가 없으므로 기본 LGD는 사용자가 바꿀 수 있는 가정값으로 둔다.
- `AMT_CREDIT`을 Exposure proxy로 사용할 경우 `Exposure Proxy`라고 표기한다.
- 학습 표본의 결과를 실제 은행 전체 고객군에 일반화하지 않는다.

---

## 4. 이해관계자와 사용 시나리오

| 사용자 | 핵심 질문 | 주요 페이지 |
|---|---|---|
| 리스크 책임자 | 손실과 고위험 Exposure가 어디에 집중되는가? | Executive, Scenario |
| 여신 포트폴리오 분석가 | 세그먼트별 관측 위험과 PD는 어떻게 다른가? | Risk Segmentation |
| 신용분석가 | 과거 연체·신용·카드 행태가 위험과 어떤 관계인가? | Repayment & Delinquency |
| 모델 리스크 담당자 | 모델 성능·교정·편향·설명력은 적절한가? | Model Monitoring |
| 채용 평가자 | 데이터 파이프라인, SQL, 분석, ML, BI가 연결되는가? | 전체 및 문서 |

---

## 5. 원천 데이터 범위와 관계

### 5.1 필수 파일

| 파일 | 기본 Grain | 주요 키 | 용도 |
|---|---|---|---|
| `application_train.csv` | 현재 신청 1건/고객 | `SK_ID_CURR` | 학습 모집단, TARGET |
| `application_test.csv` | 현재 신청 1건/고객 | `SK_ID_CURR` | 무라벨 scoring 예시 |
| `bureau.csv` | 타 금융기관 신용계좌 | `SK_ID_BUREAU`, `SK_ID_CURR` | 외부 신용이력 |
| `bureau_balance.csv` | 신용계좌 월별 상태 | `SK_ID_BUREAU`, `MONTHS_BALANCE` | 월별 연체상태 |
| `previous_application.csv` | 과거 Home Credit 신청 | `SK_ID_PREV`, `SK_ID_CURR` | 과거 신청·승인·거절 |
| `POS_CASH_balance.csv` | POS/Cash 대출 월별 잔액 | `SK_ID_PREV`, `SK_ID_CURR`, `MONTHS_BALANCE` | 월별 연체·잔여회차 |
| `credit_card_balance.csv` | 카드 월별 잔액 | `SK_ID_PREV`, `SK_ID_CURR`, `MONTHS_BALANCE` | 사용액·한도·연체 |
| `installments_payments.csv` | 예정/실제 할부 상환 | `SK_ID_PREV`, `SK_ID_CURR` | 지연일수·지급비율 |
| `HomeCredit_columns_description.csv` | 변수 설명 | 행 번호/테이블/컬럼 | 데이터 사전 보조 |
| `sample_submission.csv` | 제출 형식 | `SK_ID_CURR` | 선택 사항, 구조 확인 |

파일명 대소문자 차이를 운영체제별로 안전하게 처리한다.

### 5.2 관계 개요

```text
application (1 row per SK_ID_CURR)
  ├─ 1:N bureau
  │      └─ 1:N bureau_balance
  ├─ 1:N previous_application
  │      ├─ 1:N POS_CASH_balance
  │      ├─ 1:N credit_card_balance
  │      └─ 1:N installments_payments
  └─ TARGET (train only)
```

### 5.3 관계형 처리 규칙

- 원천 계층에서는 원래 행 단위를 보존한다.
- Feature 계층에서는 보조 테이블을 단계적으로 집계한다.
- `bureau_balance`는 먼저 `SK_ID_BUREAU`로 집계한 뒤 `bureau`에 결합하고, 이후 `SK_ID_CURR`로 다시 집계한다.
- 월별 balance 테이블은 전체기간 및 최근 구간(예: 3/6/12/24개월) 집계를 구분할 수 있다.
- `previous_application` 하위 테이블은 `SK_ID_PREV` 및 `SK_ID_CURR` 관계를 검증한다.
- 조인 전후 행 수, 고객 수, null key, 중복 key를 로그로 남긴다.
- 고객 mart에서 `SK_ID_CURR` 유일성 위반 시 파이프라인을 실패시킨다.
- 다대다 관계를 Power BI에 직접 노출하지 않고 SQL에서 적절한 fact/dimension 구조로 정규화한다.

---

## 6. 전체 아키텍처

```text
Kaggle CSV (data/raw, Git 제외)
        │
        ▼
PostgreSQL raw schema
        │  타입·행수·키·결측 검증
        ▼
PostgreSQL staging schema
        │  표준화·파생값·관계 집계
        ├──────────────┐
        ▼              ▼
analytics schema     Python EDA / Statistics
Feature Mart           │
        │               ▼
        └──────────► ML Pipeline
                        │
                        ▼
                  model_predictions
                        │
                        ▼
                  mart schema
              Star Schema + KPI Views
                        │
                        ▼
                     Power BI
              5-page decision dashboard
```

### 6.1 데이터 계층

| Schema | 목적 | 쓰기 주체 | BI 직접 연결 |
|---|---|---|---|
| `raw` | CSV 원형 보존, 최소 타입 변환 | ingestion | 금지 |
| `staging` | 정제, 표준 파생, 중간 집계 | SQL ETL | 금지 |
| `analytics` | 고객 Feature Mart, 모델 입력·출력 | SQL/Python | 제한 |
| `mart` | Power BI용 Star Schema, KPI view | SQL | 허용 |
| `audit` | 실행 이력, 품질 결과, 모델 메타데이터 | SQL/Python | 선택 |

---

## 7. 권장 저장소 구조

```text
credit-risk-portfolio/
├─ README.md
├─ .env.example
├─ .gitignore
├─ docker-compose.yml
├─ Makefile                       # Windows 대안 스크립트도 제공
├─ pyproject.toml                 # 또는 requirements.lock/requirements.txt
├─ configs/
│  ├─ project.yaml
│  ├─ features.yaml
│  └─ model.yaml
├─ data/
│  ├─ raw/.gitkeep                # 실제 CSV는 Git 제외
│  ├─ interim/.gitkeep
│  └─ processed/.gitkeep
├─ sql/
│  ├─ 00_init/
│  ├─ 01_raw/
│  ├─ 02_staging/
│  ├─ 03_analytics/
│  ├─ 04_mart/
│  ├─ 05_quality/
│  └─ 99_teardown/                # 수동 실행 전용
├─ src/
│  └─ credit_risk/
│     ├─ config.py
│     ├─ db.py
│     ├─ ingest.py
│     ├─ validate.py
│     ├─ features.py
│     ├─ split.py
│     ├─ train.py
│     ├─ evaluate.py
│     ├─ calibrate.py
│     ├─ explain.py
│     ├─ score.py
│     └─ utils.py
├─ notebooks/
│  ├─ 01_data_audit.ipynb
│  ├─ 02_eda_statistics.ipynb
│  ├─ 03_feature_research.ipynb
│  └─ 04_model_diagnostics.ipynb
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ data_quality/
│  └─ fixtures/
├─ models/                        # 바이너리 모델은 Git 정책에 따라 제외
├─ reports/
│  ├─ figures/
│  ├─ eda_report.md
│  ├─ statistical_analysis.md
│  ├─ model_card.md
│  ├─ data_dictionary.md
│  ├─ leakage_review.md
│  └─ powerbi_guide.md
├─ powerbi/
│  ├─ Credit_Risk_Portfolio.pbix
│  ├─ theme.json
│  ├─ dax_measures.md
│  ├─ data_model.md
│  └─ screenshots/
└─ scripts/
   ├─ bootstrap.ps1
   ├─ ingest.ps1
   ├─ build_marts.ps1
   ├─ train.ps1
   ├─ test.ps1
   └─ run_all.ps1
```

노트북은 탐색과 설명용이다. 재현에 필요한 핵심 로직은 반드시 `src/` 또는 `sql/`에 둔다.

---

## 8. 환경 및 Docker 명세

### 8.1 필수 구성

- PostgreSQL 16 또는 프로젝트에서 고정한 단일 버전
- Python 3.11 이상 중 고정 버전
- Power BI Desktop 최신 안정 버전(Windows)
- Docker Desktop 및 Docker Compose v2

### 8.2 `docker-compose.yml`

최소 서비스:

1. `postgres`
   - 영구 volume 사용
   - healthcheck 포함
   - 포트는 환경변수화
   - 사용자·비밀번호·DB명은 `.env`로 전달
2. 선택적 `adminer` 또는 `pgadmin`
   - 기본 프로필에서는 비활성화 가능
3. 선택적 `pipeline`
   - Python ETL/ML 실행용 이미지
   - 소스와 데이터 디렉터리를 명시적으로 mount

### 8.3 환경변수

`.env.example`에 실제 비밀값 없이 다음을 제공한다.

```dotenv
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=credit_risk
POSTGRES_USER=credit_user
POSTGRES_PASSWORD=change_me
DATA_DIR=./data/raw
MODEL_DIR=./models
RANDOM_SEED=42
```

### 8.4 실행 명령의 목표 형태

Windows PowerShell 기준으로 다음 흐름이 동작해야 한다.

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
./scripts/bootstrap.ps1
./scripts/ingest.ps1
./scripts/build_marts.ps1
./scripts/train.ps1
./scripts/test.ps1
```

가능하면 `./scripts/run_all.ps1` 한 번으로 DB 준비부터 mart 및 모델 결과 적재까지 수행한다. 각 스크립트는 재실행 가능하고 실패 시 non-zero exit code를 반환해야 한다.

---

## 9. PostgreSQL 적재 및 데이터 품질

### 9.1 적재 원칙

- 대용량 CSV는 가능하면 PostgreSQL `COPY`를 사용한다.
- 최초에는 컬럼 설명과 실제 값을 샘플링해 타입을 정의한다.
- 결측 표현(`NA`, 빈 문자열, 특수 sentinel)을 일관되게 처리한다.
- 날짜가 실제 calendar date가 아니라 상대 일수인 컬럼은 원값을 보존하고 별도 파생 컬럼을 만든다.
- `DAYS_EMPLOYED = 365243` 같은 알려진 sentinel은 null 또는 별도 flag로 처리하되 원본 컬럼도 유지한다.
- 정수 식별자는 숫자형을 유지하되 산술 Feature로 사용하지 않는다.
- 모든 적재 실행은 `audit.pipeline_run`과 `audit.table_load_log`에 기록한다.

### 9.2 필수 품질 검사

각 테이블에 대해 최소 다음을 검사한다.

- 파일 존재 여부와 예상 컬럼 존재 여부
- 행 수가 0보다 큰지
- Primary/Business key null 여부
- 예상 유일 키의 중복 수
- 외래키 orphan 비율
- `TARGET` 값이 `{0, 1}`인지
- application train에서 `SK_ID_CURR`이 유일한지
- 범주형 cardinality와 예상치 못한 신규 값
- 수치형 min/max/percentile 및 불가능한 값
- null 비율과 임계치 초과 컬럼
- 조인 전후 행 증폭률
- Mart 고객 수가 기준 application 모집단과 일치하는지

검사 결과는 단순 출력에 그치지 않고 `audit.data_quality_result`에 저장한다.

권장 컬럼:

```text
run_id, check_name, table_name, column_name,
status, observed_value, expected_rule, checked_at, details
```

### 9.3 실패 정책

- 핵심 키 중복, TARGET 범위 오류, 고객 mart grain 위반: 즉시 실패
- 일부 orphan 또는 결측 급증: 설정 임계치에 따라 warn/fail
- warning도 README 또는 품질 리포트에서 확인 가능해야 한다.

---

## 10. SQL 변환 및 Feature Mart

### 10.1 공통 Feature 규칙

- 나눗셈은 `NULLIF(denominator, 0)`을 사용한다.
- 결측률이 의미를 가질 수 있으므로 값 대치와 별개로 missing flag를 고려한다.
- 금액 비율은 극단값을 분석하고 필요 시 모델 파이프라인에서 clipping한다.
- 집계 Feature 이름은 원천·기간·통계량을 드러낸다.
- 모든 Feature에 정의, grain, source, leakage risk를 데이터 사전에 기록한다.

### 10.2 application 파생 Feature 예시

```text
age_years                  = -DAYS_BIRTH / 365.25
employment_years           = -cleaned DAYS_EMPLOYED / 365.25
credit_income_ratio        = AMT_CREDIT / AMT_INCOME_TOTAL
annuity_income_ratio       = AMT_ANNUITY / AMT_INCOME_TOTAL
credit_annuity_ratio       = AMT_CREDIT / AMT_ANNUITY
goods_credit_ratio         = AMT_GOODS_PRICE / AMT_CREDIT
income_per_person          = AMT_INCOME_TOTAL / CNT_FAM_MEMBERS
days_id_publish_years      = -DAYS_ID_PUBLISH / 365.25
days_registration_years    = -DAYS_REGISTRATION / 365.25
document_count             = 문서 flag 합계
contactability_count       = 연락처 flag 합계
external_score_mean/min/max/std = EXT_SOURCE_1~3 집계
```

### 10.3 bureau 집계 예시

`SK_ID_CURR`별:

- 전체 외부 신용계좌 수
- Active/Closed 계좌 수와 비율
- 총/평균/최대 신용한도 또는 신용금액
- 총 현재 잔액과 연체금액
- 최대 `DAYS_CREDIT_ENDDATE`, 최근 신용개설 시점
- 연장 횟수
- bureau balance 상태별 월수, 최악 상태, 최근 12개월 연체 월수

### 10.4 previous application 집계 예시

- 과거 신청 수, 승인/거절/취소 수 및 비율
- 과거 신청금액·승인금액 평균/합계/최대
- 신청 대비 승인금액 비율
- 마지막 과거 신청 이후 경과일
- 계약 유형별 신청 수
- 금리 관련 컬럼이 존재할 경우 평균/최대

### 10.5 installments 집계 예시

행 수준 파생:

```text
days_past_due  = GREATEST(DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT, 0)
days_before_due = GREATEST(DAYS_INSTALMENT - DAYS_ENTRY_PAYMENT, 0)
payment_ratio  = AMT_PAYMENT / NULLIF(AMT_INSTALMENT, 0)
payment_diff   = AMT_INSTALMENT - AMT_PAYMENT
late_flag      = days_past_due > 0
severe_late_flag = days_past_due >= 정의된 임계치
```

고객 수준 집계:

- 납부 건수, 연체 건수·비율
- 평균/최대 지연일수
- 부족납부 건수·비율 및 금액
- 지급비율 평균/최소
- 최근 기간 연체 지표

### 10.6 POS/CASH 및 credit card 집계 예시

- 월별 DPD 평균/최대, DPD 발생 월 비율
- 잔여 할부회차 평균/최대
- 카드 한도 대비 잔액 또는 인출액 비율
- 신용한도, 잔액, 인출액, 최소결제액 집계
- 최근 3/6/12개월 변화량 또는 평균
- 무한대, 분모 0, 음수 값 처리 규칙 명시

### 10.7 최종 모델 Feature Mart

`analytics.customer_feature_mart`:

- Grain: `SK_ID_CURR` 1행
- train/test source indicator 포함
- train 행에만 `TARGET` 존재
- raw application Feature와 모든 보조 테이블 집계 Feature 포함
- Feature 생성 버전 및 기준 시각 포함
- `SK_ID_CURR` primary key 또는 unique constraint

### 10.8 Power BI Star Schema

권장 테이블:

#### Fact

- `mart.fact_customer_risk`
  - Grain: 고객/현재 신청 1행
  - Key: `SK_ID_CURR`
  - `target`, `predicted_pd`, `pd_band`, `risk_grade`
  - `exposure_proxy`, `base_lgd`, `expected_loss`
  - 주요 연체·상환·신용 집계
  - 모델 버전, score date

- `mart.fact_model_evaluation`
  - Grain: model version × dataset split × metric 또는 threshold
  - ROC-AUC, PR-AUC, Brier, Log Loss, KS, precision, recall 등

- `mart.fact_calibration`
  - Grain: model version × split × PD bin
  - 평균 예측 PD, 관측률, 고객 수, Exposure

- `mart.fact_scenario_base`
  - Grain: 분석 가능한 고객 또는 집계 세그먼트
  - Base PD, Exposure proxy, Base LGD

#### Dimension

- `mart.dim_customer_segment`
  - 연령대, 소득구간, 직업군, 가족구성, 주거유형 등
- `mart.dim_risk_grade`
  - 등급, PD 하한/상한, 정렬키, 색상 hex
- `mart.dim_model_version`
  - 알고리즘, 학습일, feature version, calibration method
- 선택: `mart.dim_date` 또는 상대기간 dimension

Power BI에서는 dimension → fact 단방향 1:N 관계를 기본으로 한다. 불필요한 양방향 필터와 다대다 관계를 금지한다.

---

## 11. Python EDA 및 통계 분석

### 11.1 EDA 필수 범위

1. 테이블별 행·열·메모리·키·중복·결측률
2. TARGET 불균형 비율
3. 주요 수치형 변수의 분포와 극단값
4. 주요 범주형 변수의 빈도와 희소 범주
5. TARGET별 소득, 대출액, annuity, 비율 Feature 비교
6. 연령·고용기간·외부점수와 위험의 관계
7. 보조 테이블 집계 Feature와 TARGET 관계
8. 결측 자체와 TARGET의 관계
9. Exposure 기준 위험 집중도
10. 상관관계, 다중공선성 참고, 중복 Feature 점검

### 11.2 통계 분석 필수 항목

- 수치형: 표본 수와 분포를 고려해 t-test 또는 Mann–Whitney U, 효과크기 포함
- 범주형: Chi-square 또는 Fisher exact, Cramér's V 포함
- 구간별 관측 상환곤란율과 신뢰구간
- 다중 비교 수행 시 보정 방법 명시
- p-value만으로 중요도를 주장하지 않고 효과크기와 실무 의미를 함께 설명
- 매우 큰 표본에서 작은 차이가 유의해지는 문제를 경고

### 11.3 EDA 산출물

- `reports/eda_report.md`
- `reports/statistical_analysis.md`
- 재사용 가능한 차트 이미지
- Power BI 페이지 설계에 반영할 5~10개 핵심 인사이트
- 각 인사이트에 근거 데이터/SQL/그림 경로 연결

---

## 12. 머신러닝 및 PD 모델링

### 12.1 문제 정의

- 문제 유형: 이진 분류
- 대상: `TARGET`
- 출력: `predicted_pd` in `[0,1]`
- 기본 모집단: `application_train`에 대응하는 고객
- 핵심 평가 관점: 순위 판별력 + 확률 교정 + 포트폴리오 활용성

### 12.2 데이터 분할

기본 권장:

- Train 70%
- Validation 15%
- Holdout Test 15%
- Stratified split, `random_state` 고정
- 고객 ID 중복 없음 검증

시간 기준을 신뢰할 수 있는 calendar timestamp가 없으므로 무리하게 시간 분할이라고 주장하지 않는다. 가능한 경우 상대 시점 Feature를 활용하되, 모델 검증은 재현 가능한 stratified holdout 및 cross-validation으로 수행한다.

### 12.3 모델 계층

1. **Baseline 0**: 전체 평균 PD
2. **Baseline 1**: Logistic Regression
3. **Champion 후보**: LightGBM 또는 XGBoost/HistGradientBoosting
4. 선택: CatBoost(범주형 처리 이점을 명확히 설명할 때)

모델 선택은 성능, 교정, 해석 가능성, 실행시간을 함께 고려한다.

### 12.4 불균형 처리

- 기본적으로 class weight 또는 알고리즘의 imbalance 옵션을 검토한다.
- SMOTE는 확률 교정과 실제 분포 왜곡 위험을 설명하지 못하면 사용하지 않는다.
- PR-AUC, recall, precision을 ROC-AUC와 함께 보고한다.
- threshold는 0.5 고정이 아니라 비용 또는 운영 시나리오에 따라 별도로 분석한다.

### 12.5 평가 지표

필수:

- ROC-AUC
- PR-AUC
- Log Loss
- Brier Score
- KS statistic
- Calibration curve 및 calibration-in-the-large
- Confusion matrix at selected threshold
- Precision, Recall, F1
- Top decile/lift 또는 gains
- 고객 수뿐 아니라 Exposure-weighted risk capture

선택 threshold는 다음 중 하나로 정하고 근거를 기록한다.

- 고위험 검토 가능 고객 수(capacity)
- 목표 recall
- FN/FP 비용 가정
- Expected Loss capture 기준

### 12.6 확률 교정

- Validation 또는 CV 기반 Platt scaling/Isotonic calibration 비교
- Holdout test에 calibration fit 금지
- 교정 전후 Brier, Log Loss, calibration plot 비교
- `predicted_pd_raw`와 `predicted_pd`를 모두 보존

### 12.7 설명 가능성

- Logistic Regression coefficient/odds ratio
- Tree model global feature importance
- SHAP summary 및 dependence plot(샘플링 가능)
- 개별 고객 top reason 3~5개를 생성할 경우 방향과 기준값을 명확히 설명
- 상관 또는 SHAP 영향도를 인과로 표현하지 않는다.

### 12.8 공정성·편향 점검

민감하거나 준민감한 그룹을 자동 의사결정에 사용하라는 목적이 아니라, 모델 차이를 진단한다.

- 성별, 연령대, 가족상태 등 가능한 그룹별 AUC/recall/평균 PD/관측률 비교
- 표본이 작은 그룹은 숨김 또는 경고
- 결과를 차별의 증명이라고 단정하지 않음
- 해당 점검은 포트폴리오 프로젝트의 한계와 윤리 항목에 문서화

### 12.9 모델 아티팩트

- 직렬화된 전처리+모델 Pipeline
- feature list 및 schema hash
- model version
- split seed와 데이터 버전
- 평가 metric JSON/DB 적재
- calibration table
- scored customer table
- `reports/model_card.md`
- `reports/leakage_review.md`

---

## 13. PD · Exposure · LGD · Expected Loss

### 13.1 정의

고객 `i`의 기본 손실 추정:

```text
EL_i = PD_i × EAD_i × LGD_i
```

본 프로젝트에서는 실제 EAD와 LGD를 직접 관측하지 못하므로 다음 proxy/가정을 사용한다.

- `PD_i`: TARGET 예측 확률을 교정한 값
- `Exposure Proxy_i`: 기본값 `AMT_CREDIT`
- `LGD Assumption_i`: 기본 45% 등 명시적 사용자 가정
- `Expected Loss Proxy_i`: `PD × Exposure Proxy × LGD Assumption`

### 13.2 명명 규칙

- 보고서 카드에는 `Expected Loss (Scenario)` 또는 `예상손실(가정)`으로 표시
- 툴팁에 “실제 회수·담보 데이터가 없어 AMT_CREDIT과 가정 LGD를 사용” 명시
- 규제·회계 목적 수치로 오해할 수 있는 표현 금지

### 13.3 Risk Grade

PD band는 임의로 숨기지 않고 설정 파일/테이블로 관리한다. 초기 예시:

| Grade | PD 범위 | 표시 |
|---|---:|---|
| Low | `< 0.05` | Low |
| Medium | `0.05–<0.10` | Medium |
| High | `0.10–<0.20` | High |
| Critical | `>= 0.20` | Critical |

최종 경계는 학습 데이터 분포와 calibration을 확인한 후 고정하며, 구간별 고객 수·Exposure가 지나치게 편중되지 않는지 검증한다. 색상만으로 구분하지 않고 라벨을 병기한다.

### 13.4 포트폴리오 집계

```text
Portfolio Average PD = SUMX(Customer, PD × Exposure) / SUM(Exposure)
Expected Loss        = SUMX(Customer, PD × Exposure × LGD)
EL Rate              = Expected Loss / Total Exposure
High-Risk Exposure % = High/Critical Exposure / Total Exposure
Risk Capture         = High/Critical의 실제 TARGET=1 Exposure / 전체 TARGET=1 Exposure
```

평균 PD는 단순 고객 평균과 Exposure 가중 평균을 모두 구분하여 제공한다.

---

## 14. Power BI 데이터 모델 및 구현 원칙

### 14.1 연결 방식

- 로컬 포트폴리오 데모에서는 PostgreSQL Import mode를 기본으로 한다.
- 데이터 소스 자격 증명은 PBIX에 노출하지 않는다.
- SQL mart/view에서 복잡한 행 수준 변환을 끝내고 Power Query는 얇게 유지한다.
- Power Query 단계 이름을 의미 있게 지정한다.
- 모든 테이블과 관계를 `powerbi/data_model.md`에 문서화한다.

### 14.2 모델 규칙

- Star Schema 우선
- 숫자 surrogate key 또는 안정적 business key 사용
- dimension에서 fact로 단방향 필터
- 숫자 ID는 “요약 안 함” 설정
- 금액, 비율, 날짜/상대기간의 표시 형식 통일
- 기술 컬럼은 report view에서 숨김
- 명시적 measure 사용, 암시적 measure 지양
- KPI용 measure는 `00_Measures` 테이블에 정리
- Risk Grade 정렬은 sort key로 고정
- RLS는 선택 사항이며 구현 시 역할과 한계를 문서화

### 14.3 필수 DAX Measure 예시

실제 테이블/컬럼명에 맞게 수정하되 다음 의미를 구현한다.

```DAX
Total Customers =
DISTINCTCOUNT ( fact_customer_risk[SK_ID_CURR] )

Total Exposure =
SUM ( fact_customer_risk[exposure_proxy] )

Observed Difficulty Customers =
CALCULATE (
    [Total Customers],
    fact_customer_risk[target] = 1
)

Observed Difficulty Rate =
DIVIDE ( [Observed Difficulty Customers], [Total Customers] )

Average PD =
AVERAGE ( fact_customer_risk[predicted_pd] )

Exposure Weighted PD =
DIVIDE (
    SUMX (
        fact_customer_risk,
        fact_customer_risk[predicted_pd]
            * fact_customer_risk[exposure_proxy]
    ),
    [Total Exposure]
)

Base Expected Loss =
SUMX (
    fact_customer_risk,
    fact_customer_risk[predicted_pd]
        * fact_customer_risk[exposure_proxy]
        * fact_customer_risk[base_lgd]
)

High Risk Customers =
CALCULATE (
    [Total Customers],
    KEEPFILTERS ( dim_risk_grade[risk_group] IN { "High", "Critical" } )
)

High Risk Customer % =
DIVIDE ( [High Risk Customers], [Total Customers] )

High Risk Exposure =
CALCULATE (
    [Total Exposure],
    KEEPFILTERS ( dim_risk_grade[risk_group] IN { "High", "Critical" } )
)

High Risk Exposure % =
DIVIDE ( [High Risk Exposure], [Total Exposure] )
```

### 14.4 What-if Parameter

최소 3개 parameter를 만든다.

1. `PD Stress %`: -20%~+100%, step 5%, default 0%
2. `LGD Assumption %`: 10%~90%, step 5%, default 45%
3. `Exposure Change %`: -30%~+30%, step 5%, default 0%

필수 measure 예시:

```DAX
PD Stress Multiplier =
1 + SELECTEDVALUE ( 'PD Stress'[PD Stress Value], 0 )

Selected LGD =
SELECTEDVALUE ( 'LGD Assumption'[LGD Assumption Value], 0.45 )

Exposure Multiplier =
1 + SELECTEDVALUE ( 'Exposure Change'[Exposure Change Value], 0 )

Scenario Expected Loss =
SUMX (
    fact_customer_risk,
    MIN (
        1,
        fact_customer_risk[predicted_pd] * [PD Stress Multiplier]
    )
        * fact_customer_risk[exposure_proxy]
        * [Exposure Multiplier]
        * [Selected LGD]
)

Scenario EL Change =
[Scenario Expected Loss] - [Base Expected Loss]

Scenario EL Change % =
DIVIDE ( [Scenario EL Change], [Base Expected Loss] )
```

Parameter 값이 20%일 때 20 percentage point인지 1.2배인지 반드시 라벨과 툴팁에서 구분한다. 본 명세는 **상대 배율(1.2배)**을 기본으로 한다.

---

## 15. Power BI 5개 페이지 상세 명세

공통 캔버스는 16:9를 권장한다. 상단에 페이지 제목·기준 모집단·마지막 갱신·모델 버전을 표시하고, 공통 slicer 동작을 일관되게 유지한다.

### Page 1. Executive Portfolio Overview

**목적:** 리스크 책임자가 30초 안에 포트폴리오 크기, 위험, 손실 집중도를 파악한다.

필수 KPI 카드:

- Total Customers
- Total Exposure
- Observed Difficulty Rate
- Exposure Weighted PD
- High-Risk Exposure %
- Base Expected Loss / EL Rate

필수 시각화:

1. Risk Grade별 고객 수와 Exposure 조합 차트
2. Risk Grade별 Expected Loss 막대
3. 소득구간 또는 연령대별 Exposure와 PD
4. 고객 비중 vs Exposure 비중 비교
5. 핵심 인사이트 2~3개 텍스트 또는 dynamic narrative

필터:

- Risk Grade
- 소득구간
- 연령대
- 성별 또는 조직유형(분석 목적과 윤리 주석 포함)
- 계약/주거/직업 관련 주요 segment

상호작용:

- 모든 차트가 KPI를 교차 필터
- Reset Filters 버튼
- Risk Segmentation 페이지 drill-through 또는 navigation

### Page 2. Risk Segmentation

**목적:** 위험과 Exposure가 동시에 큰 세그먼트를 찾는다.

필수 시각화:

1. 사분면 scatter
   - X: 평균/중앙 Exposure
   - Y: 평균 PD 또는 관측 상환곤란율
   - Bubble: 고객 수 또는 총 Exposure
   - 범례: 세그먼트 또는 Risk Grade
2. Risk Grade × 주요 고객 특성 matrix/heatmap
3. 연령대·소득구간·직업군별 PD와 Exposure
4. High/Critical 세그먼트 Top N 표
5. Decomposition Tree(선택이 아닌 권장)

필수 tooltip:

- 고객 수
- Exposure
- 평균 PD
- 관측률
- Expected Loss
- 전체 포트폴리오 대비 비중

Top N 표는 조건부 서식과 data bar를 사용하고, 고객 식별정보는 노출하지 않는다.

### Page 3. Repayment & Delinquency

**목적:** 과거 상환·연체·카드·외부 신용 이력과 현재 위험의 관계를 설명한다.

필수 KPI:

- Past Due Customer %
- Average/Median Days Past Due
- Severe Delinquency Customer %
- Average Payment Ratio
- Average Credit Utilization

필수 시각화:

1. DPD 구간별 고객 수와 PD/관측률
2. Payment Ratio 구간별 관측률
3. 과거 신청 결과(Approved/Refused 등)별 위험
4. Bureau active credit 수 또는 debt ratio별 위험
5. 카드 utilization 구간별 PD와 Exposure
6. 연체 Feature 상세 table 및 tooltip

주의:

- `DAYS_*` 부호를 사람이 읽을 수 있는 지표로 변환한다.
- 집계 불가능/정보 없음과 0건을 구분한다.
- 연체가 현재 신청 이전 이력임을 확인하고 leakage review에 기록한다.

### Page 4. Model Performance & Monitoring

**목적:** 모델의 판별력, 교정, threshold trade-off, 세그먼트 안정성을 투명하게 제시한다.

필수 KPI:

- ROC-AUC
- PR-AUC
- Brier Score
- KS
- Selected Threshold
- Recall / Precision at Threshold

필수 시각화:

1. ROC curve
2. Precision-Recall curve
3. Calibration plot(예측 PD vs 관측률)
4. Confusion matrix
5. Decile lift/gains chart
6. Global Feature Importance 또는 SHAP Top 15
7. 그룹별 성능 table(표본 수 포함)

구현 주의:

- Train 성능을 메인으로 보여주지 않는다. 기본 선택은 Holdout Test.
- Model version과 split slicer를 제공한다.
- 샘플 수가 작은 calibration bin과 그룹에는 경고한다.
- 점수만 나열하지 않고 한계와 calibration 상태를 텍스트로 요약한다.

### Page 5. Scenario & Expected Loss

**목적:** PD/LGD/Exposure 변화에 따른 손실 영향을 탐색한다.

필수 What-if 입력:

- PD Stress %
- LGD Assumption %
- Exposure Change %

필수 KPI:

- Base Expected Loss
- Scenario Expected Loss
- Scenario EL Change
- Scenario EL Change %
- Scenario EL Rate

필수 시각화:

1. Base vs Scenario EL waterfall
2. Risk Grade별 시나리오 EL 변화
3. 세그먼트별 손실 기여도 stacked bar
4. PD stress × LGD 조합 sensitivity matrix
5. Top loss-contributing segments table

필수 고지문:

> 본 페이지는 AMT_CREDIT을 Exposure proxy로, 선택한 LGD를 가정값으로 사용하는 포트폴리오 민감도 분석이며 실제 충당금·규제자본 산출이 아닙니다.

---

## 16. Power BI UX·디자인 기준

- 신뢰감 있는 금융 톤: navy/blue/neutral 중심, 위험은 amber/red로 제한
- 색상은 risk grade 전체에서 일관되게 사용
- 색맹 친화 팔레트와 텍스트 라벨 병행
- 한 페이지 시각화는 정보 밀도를 고려해 6~9개 이내 권장
- 모든 축, 단위, 통화, 백분율 소수점 자릿수 통일
- K/M/B 단위 축약은 툴팁에서 전체 값 제공
- 시각화 제목은 질문 또는 결론형으로 작성
- 모든 페이지에 `Last Refreshed`, `Model Version`, `Population` 표시
- 도움말 페이지 대신 각 시각화에 짧고 정확한 tooltip 제공
- 북마크는 Reset, Definitions, Scenario Defaults에 사용 가능
- 모바일 레이아웃은 선택 사항

---

## 17. 테스트 전략

### 17.1 Unit Test

- sentinel 정제 함수
- 비율 Feature의 분모 0 처리
- DPD 및 payment ratio 계산
- risk grade boundary
- PD clipping
- EL 계산
- 설정 파일 로딩 및 필수 키 검증

### 17.2 SQL/Data Quality Test

- raw 행 수 양수
- 핵심 키 uniqueness/not null
- TARGET accepted values
- 고객 feature mart 1고객 1행
- mart와 기준 모집단 고객 수 일치
- 예측 PD 범위 `[0,1]`
- Exposure 음수/비정상값 점검
- Expected Loss가 음수가 아님
- Risk Grade null 없음(유효 PD 대상)
- 참조 무결성 및 orphan 임계치

### 17.3 Integration Test

- 소형 fixture CSV → raw 적재 → staging → feature mart → scoring → BI mart 전체 흐름
- 재실행 시 중복 적재가 발생하지 않음
- 실패 실행이 audit에 기록됨
- 모델 feature schema mismatch 시 명확한 오류

### 17.4 Model Test

- train/validation/test ID 교집합 0
- preprocessor가 train에만 fit됨
- baseline 대비 champion 성능 비교
- holdout metric 재현성 허용 오차
- calibration table 합계가 평가 표본 수와 일치
- 심각한 성능 저하 임계치가 설정 파일로 관리됨
- 모델 직렬화 후 예측 일치

### 17.5 Power BI 검증

PBIX 자동 테스트가 제한되므로 체크리스트와 SQL reconciliation을 병행한다.

- Total Customers = SQL mart distinct customers
- Total Exposure = SQL sum
- Observed Difficulty Rate = SQL 계산값
- Average/Weighted PD = SQL 계산값
- Base Expected Loss = SQL 계산값
- filter 후 주요 measure spot check
- What-if 기본값에서 Scenario EL = Base EL
- Risk Grade 합계 = 전체 합계
- 관계가 모호하거나 순환하지 않음
- 모든 페이지/버튼/tooltip/drill-through 동작
- 새로고침 성공
- 누락 visual/깨진 field 없음

검증 결과는 `reports/powerbi_validation.md`에 캡처와 함께 기록한다.

---

## 18. 문서화 산출물

### 18.1 `README.md`

반드시 포함:

- 프로젝트 소개와 사용자 질문
- 대표 Power BI 스크린샷/GIF
- 아키텍처
- 데이터셋 취득 방법과 라이선스/이용 주의
- TARGET 정의와 한계
- 빠른 시작
- 전체 재현 방법
- 폴더 구조
- 핵심 분석 인사이트
- 모델 성능 및 한계
- Expected Loss 가정
- Power BI 연결·새로고침 방법
- 테스트 실행 방법

### 18.2 데이터 사전

각 분석 Feature에 다음을 기록한다.

```text
feature_name
business_definition
technical_formula
source_table/source_columns
grain
data_type
null_handling
valid_range
leakage_risk
used_in_model
used_in_powerbi
```

### 18.3 Model Card

- intended use / out-of-scope use
- 데이터와 TARGET
- split 방식
- Feature 범위
- 모델 및 hyperparameter
- 전체/세그먼트 성능
- calibration
- threshold 선택
- 설명 가능성
- 편향/한계
- proxy PD 및 실제 운영 미적용 고지
- 재학습/버전 정보

### 18.4 Power BI Guide

- 데이터 모델 그림
- 연결 설정
- 페이지별 목적과 사용법
- DAX measure 목록
- What-if 정의
- 새로고침 절차
- SQL 대조 검증 결과
- 스크린샷 5장 이상

---

## 19. 보안·개인정보·윤리

- Kaggle 데이터라도 개인식별 가능 정보처럼 취급하고 원천 파일을 Git에 커밋하지 않는다.
- `.env`, DB dump, model binary, PBIX 내 자격 증명을 공개하지 않는다.
- 고객 ID를 보고서의 상세 표에 불필요하게 노출하지 않는다.
- 성별·연령 등 특성을 승인 자동화 근거로 권고하지 않는다.
- 분석은 교육/포트폴리오 목적임을 명시한다.
- 예측을 인과적 설명으로 포장하지 않는다.
- 실제 금융 의사결정에는 법률, 규제, 공정성, 독립 검증이 추가로 필요함을 고지한다.

---

## 20. 단계별 구현 Phase

각 Phase는 코드만 작성하고 끝내지 않는다. **산출물, 자동 검사, 짧은 실행 기록, 다음 단계 입력 준비**가 모두 완료되어야 한다.

### Phase 0 — 프로젝트 부트스트랩

작업:

- 저장소 구조 생성
- Python 환경 및 의존성 고정
- `.gitignore`, `.env.example`, config 골격
- Docker Compose/PostgreSQL healthcheck
- PowerShell 실행 스크립트 골격
- lint/test 기본 설정

완료 조건:

- PostgreSQL이 건강 상태로 기동됨
- Python package import 성공
- 빈 test suite가 아닌 최소 smoke test 통과
- README에 quick start 초안 존재

### Phase 1 — 데이터 인벤토리 및 Raw 적재

작업:

- 필수 CSV 존재/스키마 검사
- raw DDL 작성
- 대용량 bulk load
- load audit 및 row count 저장
- columns description 기반 초기 데이터 사전 생성

완료 조건:

- 모든 필수 테이블 적재
- 원본 CSV와 DB 행 수 일치
- key/null/duplicate 리포트 생성
- 재실행 안전성 검증

### Phase 2 — Staging 및 관계형 집계

작업:

- sentinel·타입·상대일 정제
- bureau → bureau_balance 2단계 집계
- previous 및 세 하위 이력 집계
- application Feature 생성
- 조인 증폭 및 orphan 검사

완료 조건:

- staging SQL이 순서대로 재실행 가능
- 각 집계 grain이 테스트로 보장됨
- `SK_ID_CURR` 기준 집계 Feature가 문서화됨

### Phase 3 — EDA 및 통계

작업:

- 품질/결측/분포/TARGET 분석
- 세그먼트·Exposure·상환행태 분석
- 통계검정과 효과크기
- BI에 사용할 핵심 인사이트 선정

완료 조건:

- EDA/통계 리포트 완성
- 재사용 가능한 분석 함수 존재
- 차트와 숫자에 재현 가능한 근거 연결

### Phase 4 — Feature Mart 및 누수 감사

작업:

- 고객 1행 Feature Mart 구축
- Feature registry/data dictionary
- leakage 후보 검토
- 모델용 허용/제외 Feature 목록 고정
- 분할 생성 및 저장

완료 조건:

- 고객 key 유일성 통과
- split 교집합 0
- `leakage_review.md` 승인 가능한 수준으로 완성
- 허용 Feature만 모델 config에 포함

### Phase 5 — 모델 개발·교정·설명

작업:

- baseline 및 champion 후보
- CV/validation 기반 선택
- holdout 최종 평가
- calibration 비교
- threshold 및 risk grade 제안
- SHAP/feature importance
- 세그먼트 성능 점검

완료 조건:

- baseline 대비 개선 근거
- 필수 metric과 calibration 산출
- 전체 Pipeline 직렬화/재로딩 검증
- Model Card 초안 및 prediction table 생성

### Phase 6 — PD/EL 및 BI Mart

작업:

- 예측 결과 DB 적재
- Exposure proxy 및 LGD 가정 구현
- EL 계산 및 검증
- Star Schema와 KPI view
- Power BI용 SQL reconciliation query

완료 조건:

- BI mart가 원천 모집단과 대조됨
- PD/EL 범위 및 합계 검증
- 모델 버전 추적 가능
- Power BI 연결 준비 완료

### Phase 7 — Power BI 5페이지

작업:

- theme, relationship, measure table
- Executive/Risk/Repayment/Model/Scenario 페이지
- What-if Parameter
- tooltip, drill-through, navigation, reset
- 성능 최적화

완료 조건:

- 5개 페이지 요구사항 충족
- 모든 핵심 수치 SQL과 일치
- 기본 시나리오가 base 값과 일치
- 스크린샷 및 사용 가이드 생성

### Phase 8 — 통합 테스트·문서·포트폴리오 마감

작업:

- clean environment 전체 실행
- 테스트 및 품질 리포트
- README, 데이터 사전, 모델 카드, BI guide 마감
- 대표 스크린샷/GIF
- 알려진 한계와 향후 개선 정리

완료 조건:

- 신규 사용자가 문서만 보고 재현 가능
- 민감정보/대용량 원천 데이터가 Git에 없음
- 모든 DoD 항목 충족

---

## 21. Claude Code 작업 규칙

Claude Code는 아래 규칙을 프로젝트의 실행 계약으로 따른다.

### 21.1 시작 전

1. 현재 repository 상태와 기존 파일을 먼저 확인한다.
2. 기존 사용자 변경을 덮어쓰지 않는다.
3. 현재 Phase, 목표, 입력, 예상 산출물, 검증 명령을 짧게 제시한다.
4. 불명확한 업무 정의는 가정으로 기록하고, 데이터로 확인 가능한 부분을 먼저 확인한다.
5. 대규모 변경 전 관련 파일과 데이터 schema를 읽는다.

### 21.2 구현 중

1. 한 번에 한 Phase 또는 검증 가능한 작은 slice를 완료한다.
2. Notebook에만 핵심 로직을 남기지 않는다.
3. 반복 가능한 SQL/Python 모듈과 config를 우선한다.
4. 데이터 grain과 조인 cardinality를 코드 주석 및 문서에 명시한다.
5. `TARGET` 및 파생 레이블이 Feature에 유입되지 않도록 자동 검사한다.
6. fit/transform 경계를 Pipeline으로 강제한다.
7. 파일 경로, 비밀번호, 포트, threshold를 하드코딩하지 않는다.
8. 큰 테이블을 무분별하게 메모리에 모두 로드하지 않는다. dtype, chunk, DB 집계를 활용한다.
9. 무거운 연산은 진행 로그와 실행시간을 남긴다.
10. 코드 변경과 함께 관련 테스트·문서를 갱신한다.
11. 실제 데이터 결과를 확인하기 전 숫자, 성능, 인사이트를 만들어내지 않는다.
12. 실제 규제 모델처럼 과장하지 않는다.

### 21.3 검증 규칙

각 작업 후 최소 다음을 수행한다.

- 변경 파일 lint/format
- 관련 unit/integration/data test
- SQL row count, key uniqueness, join reconciliation
- 모델 변경 시 leakage/split/metric 재검증
- BI mart 변경 시 DAX/SQL 핵심 지표 대조

테스트를 실행하지 못했다면 이유와 미검증 범위를 명시한다. “완료”라고 표현하려면 완료 기준을 실제로 확인해야 한다.

### 21.4 커뮤니케이션 규칙

각 작업 보고는 다음 순서를 권장한다.

```text
완료한 결과
변경한 파일
검증 결과
남은 위험/가정
다음 권장 작업
```

장황한 구현 일지보다 의사결정과 검증 결과를 우선한다.

### 21.5 금지 사항

- 원천 CSV를 Git에 커밋
- 테스트 성능을 보고 모델을 반복 선택
- 전체 데이터 fit 후 split
- TARGET을 포함한 집계 또는 이름만 바꾼 누수 Feature 사용
- 보조 테이블을 그대로 application에 조인해 고객 행을 증폭
- `application_test`를 내부 holdout test로 오해
- 실제 LGD/EAD가 있는 것처럼 표현
- SQL과 DAX 양쪽에 서로 다른 업무 규칙 중복 구현
- 테스트 실패를 숨기거나 임의로 삭제
- 모델 성능 수치를 실행 없이 문서에 기입
- PBIX만 남기고 DAX/모델 구조를 문서화하지 않음

---

## 22. Definition of Done

아래 항목을 모두 만족해야 프로젝트 완료로 간주한다.

### Data & Engineering

- [ ] Home Credit 필수 관계형 파일이 PostgreSQL raw schema에 적재된다.
- [ ] raw/staging/analytics/mart/audit 계층이 분리된다.
- [ ] 행 수, 키, 결측, orphan, TARGET 품질 검사가 자동화된다.
- [ ] 보조 테이블이 올바른 grain으로 집계된다.
- [ ] 고객 Feature Mart는 `SK_ID_CURR` 1행을 보장한다.
- [ ] 전체 파이프라인은 재실행 가능하다.

### Analysis & Statistics

- [ ] EDA가 포트폴리오, 세그먼트, Exposure, 상환행태를 다룬다.
- [ ] 통계 검정에 효과크기와 해석 한계가 포함된다.
- [ ] 핵심 인사이트가 Power BI 시각화로 연결된다.

### Machine Learning

- [ ] TARGET 의미와 proxy PD 한계가 명시된다.
- [ ] Train/validation/holdout 분리가 재현 가능하다.
- [ ] 전처리가 split 이후 train fold에만 fit된다.
- [ ] leakage review와 자동 검사가 존재한다.
- [ ] baseline과 champion이 비교된다.
- [ ] ROC-AUC, PR-AUC, Brier, Log Loss, KS, calibration이 보고된다.
- [ ] 확률 교정과 threshold 선정 근거가 있다.
- [ ] 모델 설명과 세그먼트 점검이 있다.
- [ ] 모델 카드가 완성된다.

### Risk & Scenario

- [ ] 고객별 calibrated PD가 저장된다.
- [ ] Exposure proxy가 명확히 정의된다.
- [ ] LGD가 가정값임을 명시한다.
- [ ] Base/Scenario Expected Loss가 재현 가능하다.
- [ ] PD/LGD/Exposure What-if가 정상 동작한다.

### Power BI

- [ ] Star Schema와 관계가 문서화된다.
- [ ] 5개 필수 페이지가 완성된다.
- [ ] KPI와 필수 DAX가 구현된다.
- [ ] Executive 페이지에서 위험 집중도를 즉시 파악할 수 있다.
- [ ] Model 페이지는 holdout 성능과 calibration을 보여준다.
- [ ] Scenario 페이지는 가정과 한계를 고지한다.
- [ ] 주요 KPI가 SQL 결과와 일치한다.
- [ ] 새로고침, slicer, tooltip, navigation, reset이 작동한다.
- [ ] 각 페이지 스크린샷이 저장된다.

### Reproducibility & Documentation

- [ ] Docker Compose로 PostgreSQL이 기동된다.
- [ ] `.env.example`과 Windows 실행 스크립트가 있다.
- [ ] clean setup부터 mart/model 결과까지 실행 방법이 검증된다.
- [ ] unit/integration/data/model 테스트가 통과한다.
- [ ] README, 데이터 사전, EDA, 통계, leakage review, model card, BI guide가 완성된다.
- [ ] 원천 데이터와 비밀정보가 Git에 포함되지 않는다.
- [ ] 교육용·비규제용 한계가 명시된다.

---

## 23. 최종 포트폴리오 데모 시나리오

5~7분 데모 순서:

1. **문제 정의:** 단순 부도예측이 아니라 포트폴리오 위험 의사결정 도구임을 설명
2. **데이터 엔지니어링:** 8개 핵심 관계형 파일, PostgreSQL 계층, 고객 grain 통합
3. **Executive:** 고객 비중과 Exposure 비중 차이, 고위험 집중도 제시
4. **Risk/Repayment:** 위험을 설명하는 세그먼트와 과거 상환행태 탐색
5. **Model:** 누수 방지, holdout 성능, calibration, 설명 가능성 제시
6. **Scenario:** PD/LGD/Exposure stress에 따른 EL 변화 시연
7. **한계:** TARGET/Exposure/LGD proxy와 실제 운영 전 추가 검증 필요성 설명

데모의 결론은 “모델 AUC가 높다”가 아니라 다음이어야 한다.

> 관계형 신용 데이터를 신뢰 가능한 고객 단위로 통합하고, 검증·교정된 위험 점수를 포트폴리오 Exposure 및 손실 시나리오와 결합하여 Power BI에서 실제 의사결정 질문에 답할 수 있게 했다.

---

## 24. 선택적 고도화 백로그

필수 DoD 완료 후에만 진행한다.

- Optuna 기반 제한적 hyperparameter tuning
- Population Stability Index 및 score drift simulation
- Vintage/cohort 분석(데이터 해석이 타당한 범위)
- Reject inference 개념 검토(구현 시 강한 가정 명시)
- SHAP reason code의 BI drill-through
- dbt 도입 및 data lineage 문서화
- Great Expectations/Pandera 기반 품질 프레임워크
- MLflow 기반 experiment/model registry
- Power BI incremental refresh(날짜 요건이 충족될 때)
- CI에서 unit/SQL style/data fixture test 자동화
- 민감도 분석을 위한 Monte Carlo loss simulation

필수 기능을 희생해 이 백로그부터 구현하지 않는다.

---

## 25. Claude Code 시작 프롬프트

아래 문구를 새 Claude Code 세션의 첫 요청으로 사용할 수 있다.

```text
이 저장소의 CREDIT_RISK_LOAN_PORTFOLIO_SPEC_KO.md를 전체 읽고 실행 계약으로 사용해라.

먼저 현재 파일 구조와 Git 상태를 확인하고, 사용자 변경을 보존해라. 그 다음 Phase 0만 구현해라. 구현 전에 Phase 0의 목표, 생성/수정할 파일, 검증 방법을 짧게 제시하고 바로 작업을 진행해라.

중요 규칙:
- 최종 핵심 산출물은 Power BI 5페이지다.
- application_train의 TARGET 의미를 정확히 문서화하고 proxy PD 한계를 유지한다.
- 현재 신청 시점 이후 정보 또는 TARGET 파생정보가 모델에 유입되지 않게 한다.
- split 전에 전처리를 fit하지 않는다.
- 관계형 하위 테이블은 올바른 키로 먼저 집계해 SK_ID_CURR 1행을 보장한다.
- 실제 EAD/LGD가 없으므로 AMT_CREDIT과 LGD 가정을 실제 관측값처럼 표현하지 않는다.
- 노트북에만 핵심 로직을 두지 않는다.
- 각 변경 후 관련 테스트를 실행하고 결과를 보고한다.
- 실제 실행 전 성능 수치나 분석 결론을 만들어내지 않는다.

Phase 0 완료 기준을 실제로 검증한 뒤, 변경 파일·검증 결과·남은 위험·Phase 1 권장 작업을 요약하고 멈춰라.
```

