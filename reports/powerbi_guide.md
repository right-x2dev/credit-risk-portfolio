# Power BI Guide — Credit Risk Portfolio

> 교육·포트폴리오 목적의 보고서입니다. PD는 Home Credit `TARGET`을 예측한 **proxy PD**이고, Exposure는 `AMT_CREDIT` proxy이며, LGD는 가정값입니다. 실제 충당금·규제자본 수치가 아닙니다.

## 1. 파일 구성
| 경로 | 내용 |
|---|---|
| `powerbi/Credit_Risk_Portfolio.pbip` | Power BI Project 진입점(Desktop에서 열기) |
| `powerbi/Credit_Risk_Portfolio.SemanticModel/` | TMDL 시맨틱 모델(테이블·관계·61개 measure·What-if) |
| `powerbi/Credit_Risk_Portfolio.Report/` | PBIR 보고서(5페이지, 테마, Reset 북마크) |
| `powerbi/theme.json` | navy/neutral 금융 톤 테마, 위험은 amber/red로 제한 |
| `powerbi/data_model.md` | Star schema·관계·What-if 정의(자동 생성) |
| `powerbi/dax_measures.md` | 모든 DAX measure 정의(자동 생성) |
| `powerbi/screenshots/` | 5개 페이지 캡처(`scripts/capture_pages.ps1`) |

PBIP는 `credit-risk build-pbip`가 **live `mart` 스키마를 읽어 생성**합니다. 그래서 SQL 컬럼이 바뀌면 보고서 모델도 함께 재생성되고, DAX 문서와 모델이 어긋나지 않습니다.

## 2. 연결 설정
1. `./scripts/run_all.ps1` 실행 → PostgreSQL `mart` 스키마와 CSV 스냅샷(`data/processed/powerbi/`)이 준비됩니다.
2. `powerbi/Credit_Risk_Portfolio.pbip`를 Power BI Desktop에서 엽니다.
3. **홈 → 새로 고침**을 누릅니다.
   - 기본 `DataSource = PostgreSQL`(Import): 처음 한 번 데이터베이스 자격 증명 창이 뜨면 `.env`의 `POSTGRES_USER` / `POSTGRES_PASSWORD`를 입력합니다. 자격 증명은 PBIP/PBIX가 아닌 Desktop 로컬 저장소에만 저장됩니다.
   - 자격 증명 없이 보려면 **홈 → 데이터 변환 → 매개 변수 편집**에서 `DataSource = CSV`로 바꿉니다(`CsvFolder`는 스냅샷 경로).
4. 필요하면 **파일 → 다른 이름으로 저장 → `Credit_Risk_Portfolio.pbix`**로 바이너리 파일을 만듭니다(Git에는 PBIP만 커밋).

## 3. 페이지별 목적과 사용법
| Page | 질문 | 사용법 |
|---|---|---|
| 1 Executive Overview | 포트폴리오 크기·위험·손실 집중도 | 7개 KPI → grade별 고객/Exposure/EL → 고객 비중 vs Exposure 비중 → 소득·연령별 Exposure와 가중 PD. 하단 narrative는 필터에 따라 동적으로 바뀜 |
| 2 Risk Segmentation | 위험과 Exposure가 동시에 큰 세그먼트 | 직업군 사분면(평균 Exposure × 가중 PD, 버블=Exposure, tooltip=고객 수·관측률·EL·비중), 소득×grade heatmap, High/Critical Top 세그먼트 표(data bar), Decomposition tree |
| 3 Repayment & Delinquency | 과거 상환·연체·카드·외부신용 이력과 현재 위험 | 신청 **이전** 이력만 사용. “이력 없음” 구간은 0건과 분리 |
| 4 Model Performance | 판별력·교정·threshold·세그먼트 | split slicer 기본값 **Holdout**. ROC/PR, calibration(교정 후), confusion matrix, lift/gains, SHAP top 15, 그룹 진단(소표본 플래그) |
| 5 Scenario & Expected Loss | PD/LGD/Exposure 악화 시 손실 | What-if slicer 3개(선택 없음 = 기본 0%/45%/0%), Base→Scenario waterfall, grade별 EL, 소득×grade 기여도, PD×LGD 민감도 matrix, Top 손실 세그먼트 |

공통: 상단 page navigator, **Reset filters**(페이지별 북마크), 헤더의 Population · Model version · Last refreshed.
합성 데이터로 생성된 경우 헤더에 `SYNTHETIC TEST DATA` 경고가 표시됩니다.

## 4. DAX measure 목록
`powerbi/dax_measures.md`를 참조하세요(Portfolio · Repayment · Model · Scenario · Narrative · Metadata 폴더).
핵심 규칙: Exposure proxy·LGD·EL·Risk grade·연체 구간은 **SQL에서 한 번만 정의**하고, DAX는 집계와 What-if 배율만 담당합니다.

## 5. What-if 정의
- `PD Stress %`: −20%~+100%, step 5%, 기본 0% → `MIN(1, PD × (1 + 값))`
- `LGD Assumption %`: 10%~90%, step 5%, 기본 45%
- `Exposure Change %`: −30%~+30%, step 5%, 기본 0% → `Exposure × (1 + 값)`
- 모든 값은 **상대 배율**입니다(+20% = ×1.2, +20 percentage point가 아님).
- Waterfall은 Base EL → PD stress 효과 → LGD 변경 효과 → Exposure 변경 효과 순서로 분해합니다(합계 = Scenario EL).

## 6. 새로고침 절차
```powershell
./scripts/run_all.ps1          # 데이터·모델·mart·CSV 스냅샷·PBIP 재생성
# Power BI Desktop에서 PBIP 열기 → 새로 고침
./scripts/powerbi_check.ps1    # (선택) TMDL/PBIR 검증 + DAX↔SQL 대조 + 스크린샷
```

## 7. SQL 대조 검증 결과
`reports/powerbi_validation.md`에 있습니다. Power BI 엔진이 계산한 DAX와 SQL mart를 대조하며, 최근 실행 결과는 42/42 일치입니다(합성 데이터 기준).

## 8. 스크린샷
![Executive](../powerbi/screenshots/p1_executive.png)
![Segmentation](../powerbi/screenshots/p2_segmentation.png)
![Repayment](../powerbi/screenshots/p3_repayment.png)
![Model](../powerbi/screenshots/p4_model.png)
![Scenario](../powerbi/screenshots/p5_scenario.png)

## 9. 제한 사항
- RLS는 구현하지 않았습니다(단일 분석가용 데모).
- 모바일 레이아웃은 제공하지 않습니다(명세상 선택 사항).
- 고객 식별자는 visual에 노출하지 않습니다(`sk_id_curr`은 숨김 컬럼).
