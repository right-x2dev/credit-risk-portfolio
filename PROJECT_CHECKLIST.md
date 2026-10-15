# 프로젝트 진행 체크리스트

> 기준 문서: `CREDIT_RISK_LOAN_PORTFOLIO_SPEC_KO.md` (v1.0)
> 표기: `[x]` 완료 · `[ ]` 미완료 · `[~]` 부분 완료/사용자 작업 필요 · 👤 = 사용자가 직접 해야 하는 항목
> 선택이 필요한 지점은 모두 명세서 권장안(recommended)으로 결정했으며, 결정 내역은 하단 "의사결정 로그"에 기록한다.

## Phase 0 — 프로젝트 부트스트랩
- [x] 저장소 구조 생성 (configs/data/sql/src/notebooks/tests/models/reports/powerbi/scripts)
- [x] Python 3.12 가상환경 + 의존성 고정 (`pyproject.toml`, `requirements.lock`)
- [x] `.gitignore`, `.env.example`, `configs/*.yaml` 골격
- [x] `docker-compose.yml` (PostgreSQL 16 + healthcheck, adminer/pipeline 선택 프로필)
- [x] PowerShell 스크립트 (`bootstrap/ingest/build_marts/train/test/run_all.ps1`)
- [x] lint(ruff)/pytest 기본 설정 + smoke test 통과
- [x] PostgreSQL 컨테이너 healthy 기동 확인
- [x] README quick start 초안

## Phase 1 — 데이터 인벤토리 및 Raw 적재
- [~] 👤 Kaggle Home Credit 데이터 다운로드 → `data/raw/` (계정·대회 규칙 동의 필요) — **사용자 작업 대기.** 받은 뒤 `./scripts/run_all.ps1` 한 번이면 모든 산출물이 실제 데이터로 재생성됨
- [x] 스키마 동일 합성 fixture 데이터 생성기 (실데이터 없이 전체 흐름 검증용)
- [x] 필수 CSV 존재/컬럼 검사 (파일명 대소문자 무관)
- [x] raw DDL 자동 생성 + `COPY` bulk load
- [x] `audit.pipeline_run`, `audit.table_load_log` 기록
- [x] CSV 행 수 = DB 행 수 검증
- [x] key/null/duplicate/orphan 품질 검사 → `audit.data_quality_result`
- [x] columns description 기반 초기 데이터 사전
- [x] 재실행 안전성(멱등성) 검증

## Phase 2 — Staging 및 관계형 집계
- [x] sentinel(365243)·XNA·상대일 정제 (원본 컬럼 보존)
- [x] bureau_balance → bureau → SK_ID_CURR 2단계 집계
- [x] previous_application 및 POS/installments/credit_card 집계
- [x] application 파생 Feature
- [x] 신청 시점 이후 레코드(양수 DAYS/MONTHS) 필터링
- [x] 조인 증폭·orphan·grain 검사

## Phase 3 — EDA 및 통계
- [x] 재사용 가능한 분석 함수 (`src/credit_risk/eda.py`, `stats.py`)
- [x] 품질/결측/분포/TARGET/세그먼트/Exposure/상환행태 분석
- [x] 통계 검정(Mann–Whitney, Chi-square) + 효과크기 + 다중비교 보정 + 신뢰구간
- [x] `reports/eda_report.md`, `reports/statistical_analysis.md`, `reports/figures/`
- [x] Power BI 반영 핵심 인사이트 5~10개 (근거 경로 포함)
- [x] 노트북 01~04 (설명용, 핵심 로직은 src/)

## Phase 4 — Feature Mart 및 누수 감사
- [x] `analytics.customer_feature_mart` (SK_ID_CURR PK, 버전·기준시각)
- [x] Feature registry / `reports/data_dictionary.md`
- [x] `reports/leakage_review.md` + 자동 누수 검사
- [x] 허용/제외 Feature 목록 `configs/features.yaml` 고정
- [x] 70/15/15 stratified split 저장 + ID 교집합 0 검증

## Phase 5 — 모델 개발·교정·설명
- [x] Baseline 0 (평균 PD), Baseline 1 (Logistic Regression)
- [x] Champion (LightGBM) — validation 기반 선택
- [x] Platt vs Isotonic 교정 비교 (holdout fit 금지)
- [x] Holdout 1회 최종 평가 (ROC-AUC, PR-AUC, LogLoss, Brier, KS, calibration, lift, EL capture)
- [x] Threshold(capacity 기준) + Risk grade 경계 검증
- [x] Feature importance / SHAP / reason code
- [x] 세그먼트 공정성·편향 점검
- [x] Pipeline 직렬화 → 재로딩 예측 일치
- [x] `reports/model_card.md`

## Phase 6 — PD/EL 및 BI Mart
- [x] 예측 결과 DB 적재 (`predicted_pd_raw`, `predicted_pd`)
- [x] Exposure proxy(AMT_CREDIT) + LGD 가정(45%) + EL 계산
- [x] Star Schema (fact 4 + dim 3) 및 KPI view
- [x] SQL reconciliation 쿼리 + 검증
- [x] Power BI용 mart 스냅샷 export

## Phase 7 — Power BI 5페이지
- [x] theme.json (navy/neutral, 색맹 친화 risk 색상)
- [x] 시맨틱 모델(TMDL): 관계, `00_Measures`, What-if 3종
- [x] 보고서(PBIR) 5페이지: Executive / Risk Segmentation / Repayment / Model / Scenario
- [x] tooltip, navigation, Reset 북마크
- [x] `powerbi/dax_measures.md`, `powerbi/data_model.md`, `reports/powerbi_guide.md`
- [x] Power BI Desktop에서 열기·새로고침·스크린샷
- [~] 👤 (선택) Desktop에서 PBIP 열기 → 새로 고침 → `Credit_Risk_Portfolio.pbix`로 저장, Reset 북마크·slicer 클릭 동작 눈으로 확인
- [x] `reports/powerbi_validation.md` (DAX vs SQL 대조)

## Phase 8 — 통합 테스트·문서·마감
- [x] unit / integration / data_quality / model 테스트 통과
- [x] clean environment `run_all.ps1` 전체 실행 검증
- [x] README 완성 (아키텍처, 재현, 인사이트, 성능, 한계, 윤리)
- [x] 민감정보·원천 데이터 Git 제외 확인
- [x] GitHub 저장소 생성 및 push

## 검증 기록 (합성 데이터 기준, 2026-10-16)
- `run_all.ps1 -Synthetic`: 빈 DB → 전체 파이프라인 2.9분, 모든 단계 OK
- pytest 68 passed (unit · integration E2E · model · data_quality · stats), ruff clean
- 품질 검사: raw / staging / analytics / model / mart 단계 fail 0
- Power BI: TMDL 파싱 OK(21 테이블·19 관계·61 measure), PBIR 공식 스키마 0 error, Desktop 오류 대화상자 없이 열림,
  로컬 엔진 배포·새로고침 21/21 Ready, **DAX ↔ SQL 대조 42/42 일치**, 5페이지 스크린샷 저장
- Docker: pipeline 이미지 빌드 및 컨테이너 → PostgreSQL 연결 확인, 노트북 4개 실행 확인

## 의사결정 로그 (권장안 자동 선택)
| 결정 사항 | 선택 | 근거 |
|---|---|---|
| Python 버전 | 3.12.13 (uv 관리 `.venv`) | 명세 3.11+ 고정 버전, 주요 ML 휠 호환 |
| PostgreSQL | `postgres:16.4` Docker 이미지 | 명세 16 권장, 단일 버전 고정 |
| 의존성 고정 | `pyproject.toml` + `requirements.lock`(uv universal) | Windows/Linux(Docker) 동시 재현 |
| Raw 적재 방식 | 클라이언트 측 `COPY FROM STDIN` + 원자적 테이블 교체 | 서버 파일 마운트 불필요, 재실행 안전 |
| Raw 타입 | 전체 스캔 추론: ID→bigint, TARGET→smallint, 숫자→double, 그 외 text, 컬럼명 소문자 | 원형 보존 + 따옴표 없는 SQL |
| 신청 시점 경계 | bureau_balance month 0 제외, 모든 이력 `<= 0`/`< 0`, 사후 날짜 NULL 처리 | 누수 방지 보수적 선택 |
| Installments 집계 | 납부행 → 회차(instalment) → 고객 2단계 | 부분납부로 인한 DPD·지급비율 왜곡 방지 |
| 심각 연체 기준 | DPD ≥ 30일 (`configs/project.yaml`) | 업계 통상 30+ DPD |
| 모델 계층 | Baseline 평균 / Logistic Regression / LightGBM | 명세 12.3 권장 |
| 불균형 처리 | class weight·SMOTE 미사용 | 확률 교정 유지(명세 12.4), PR-AUC 병행 보고 |
| Early stopping | Train 내부 10% slice | Validation/Holdout 오염 방지 |
| 교정 방법 선택 | Validation 내부 5-fold CV로 Platt vs Isotonic 비교(Brier) | Holdout fit 금지(명세 12.6) |
| Threshold 정책 | Review capacity: Validation PD 상위 10% | 명세 12.5 옵션 중 운영 설명이 가장 명확 |
| Risk Grade 경계 | 명세 초기값 5/10/20% (config) | 분포 확인 후 유지 여부 기록 |
| LGD 기본값 | 45% 가정 | 명세 13.1 예시 |
| Power BI 형식 | PBIP(TMDL+PBIR) 텍스트 프로젝트 → Desktop에서 열어 PBIX 저장 | Git diff 가능, 코드로 재현 가능 |
| Power BI 데이터 소스 | 기본 PostgreSQL Import, 파라미터로 CSV 스냅샷 전환 가능 | 명세 14.1 + 자격증명 없이 데모 가능 |
| EDA 구현 | 서브 에이전트 병렬 작성(`eda.py`, `stats.py`) | 일정 단축 |
| Power BI 검증 방식 | Desktop 내장 TmdlSerializer·로컬 AS 엔진으로 헤드리스 배포/DAX 대조, UI Automation으로 오류 확인, PrintWindow 캡처 | 사용자 부재(잠금 화면)에서도 검증 가능 |
| 네비게이션 | Power BI 기본 page navigator + 페이지별 Reset 북마크 navigator | 커스텀 버튼 텍스트 렌더링 문제 회피 |
| GitHub 공개 범위 | Public 저장소 (원천 데이터·.env·모델 바이너리 제외) | 포트폴리오 목적 |
