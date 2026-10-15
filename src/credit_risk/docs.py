"""Generated documentation: data dictionary and leakage review (spec §18.2, Phase 4)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Settings
from .db import connect, read_frame
from .reporting import synthetic_banner

_TOKENS = {
    "bur": "external bureau credits", "prev": "previous Home Credit applications", "inst": "instalment payments",
    "pos": "POS/cash loan months", "cc": "credit card months", "bb": "bureau monthly status", "count": "count",
    "mean": "mean", "max": "maximum", "min": "minimum", "sum": "sum", "total": "total", "ratio": "share/ratio",
    "dpd": "days past due", "dpd30": "30+ DPD", "12m": "last 12 months", "6m": "last 6 months", "2y": "last 2 years",
    "active": "active", "closed": "closed", "refused": "refused", "approved": "approved", "late": "late",
    "severe": "severe (>= threshold DPD)", "utilization": "balance / limit", "amt": "amount", "days": "days",
    "since": "since", "last": "last", "first": "first", "debt": "debt", "credit": "credit", "overdue": "overdue",
    "has": "has history in", "installments": "instalment payments", "previous": "previous applications",
    "bureau": "credit bureau", "pos_cash": "POS/cash", "credit_card": "credit card",
}


def sql_formulas(sql_dir: Path) -> dict[str, str]:
    """Extract `<expression> AS <alias>` lines from the staging SQL as technical formulas."""
    out: dict[str, str] = {}
    pat = re.compile(r"^\s*(?P<expr>.+?)\s+AS\s+(?P<alias>[a-z_0-9]+)\s*,?\s*(--.*)?$", re.IGNORECASE)
    for path in sorted((sql_dir / "02_staging").glob("*.sql")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = pat.match(line)
            if m and not m.group("expr").strip().upper().startswith(("CREATE", "SELECT", "WITH", "DROP", "FROM")):
                out.setdefault(m.group("alias"), f"{m.group('expr').strip()}  [{path.name}]")
    return out


def humanise(name: str) -> str:
    return " ".join(_TOKENS.get(p, p) for p in name.split("_"))


def write_data_dictionary(settings: Settings) -> Path:
    with connect(settings) as conn:
        reg = read_frame(conn, "SELECT * FROM analytics.feature_registry")
        num_cols = reg.loc[reg["data_type"].isin(["double precision", "bigint", "integer", "smallint", "numeric"]),
                           "feature_name"].tolist()
        rng: dict[str, tuple] = {}
        if num_cols:
            expr = ", ".join(f'min("{c}"), max("{c}")' for c in num_cols)
            row = conn.execute(f"SELECT {expr} FROM analytics.customer_feature_mart WHERE source='train'").fetchone()
            rng = {c: (None if row[2 * i] is None else float(row[2 * i]),
                       None if row[2 * i + 1] is None else float(row[2 * i + 1])) for i, c in enumerate(num_cols)}
        bi_cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='mart' "
            "AND table_name='fact_customer_risk'").fetchall()}
    formulas = sql_formulas(settings.sql_dir)
    lines = [
        f"# Data Dictionary — analytics.customer_feature_mart ({settings.feature_version})", "",
        synthetic_banner(settings) + "_자동 생성: `credit-risk docs`. grain = 1 row per `SK_ID_CURR`. 원천 컬럼 설명은 "
        "Kaggle `HomeCredit_columns_description.csv`에서, 파생/집계 Feature의 기술 공식은 `sql/02_staging/*.sql`에서 "
        "자동 추출한다._", "",
        "**공통 규칙:** 나눗셈은 `NULLIF(den, 0)`; 집계 NULL = 해당 원천 이력 없음(0건과 구분, `has_*` flag 병기); "
        "이력은 신청 시점 이전(`DAYS_* <= 0`, `MONTHS_BALANCE < 0`)만 사용; 모델 결측 대치는 Pipeline 내부에서만 수행.", "",
        "| feature_name | business_definition | technical_formula | source | grain | data_type | null_ratio (train) "
        "| valid_range (train min..max) | leakage_risk | used_in_model | used_in_powerbi |",
        "|---|---|---|---|---|---|---:|---|---|---|---|",
    ]
    for _, r in reg.iterrows():
        name = r["feature_name"]
        raw_desc = r["raw_description"]
        desc = raw_desc if isinstance(raw_desc, str) and raw_desc else humanise(name)
        desc = desc.replace("|", "/").replace("\n", " ")[:160]
        is_raw_app = str(r["source"]).startswith("application")
        formula = "raw column (lower-cased)" if is_raw_app else formulas.get(name, "")
        if formula[:1] in "/+-*":
            formula = "… " + formula
        used_model = str(r["used_in_model"]).lower() in ("t", "true", "1")
        source = str(r["source"]).replace("|", "\\|")
        formula = formula.replace("|", "\\|")[:180]
        lo, hi = rng.get(name, (None, None))
        vr = "" if lo is None else f"{lo:.4g} .. {hi:.4g}"
        lines.append(
            f"| `{name}` | {desc} | `{formula}` | {source} | SK_ID_CURR | {r['data_type']} | "
            f"{r['null_ratio_train']:.3f} | {vr} | {r['leakage_risk']} | {'Y' if used_model else 'N'} | "
            f"{'Y' if name in bi_cols else 'N'} |")
    lines += [
        "", "## Null handling", "",
        "- Raw: 빈 문자열만 NULL로 적재(문자 'NA'는 값으로 보존).",
        "- Staging: `DAYS_EMPLOYED=365243` → `days_employed_clean` NULL + `days_employed_anom=1`; previous "
        "`DAYS_*=365243` → `*_clean` NULL; `CODE_GENDER='XNA'`, `NAME_FAMILY_STATUS='Unknown'` → NULL.",
        "- Model: LightGBM은 NaN을 기본 처리, Logistic Regression은 Train-fit median 대치 + missing indicator; "
        "범주형은 'MISSING' 상수(LR) 또는 OrdinalEncoder 결측 코드(LightGBM).",
        "", "## Power BI fact columns (mart.fact_customer_risk)", "",
        "| column | definition |", "|---|---|",
        "| `exposure_proxy` | `AMT_CREDIT` — Exposure proxy (실제 EAD 아님) |",
        "| `base_lgd` | 가정 LGD (`configs/project.yaml: risk.base_lgd`) |",
        "| `expected_loss` | `predicted_pd × exposure_proxy × base_lgd` — 시나리오 지표, 충당금 아님 |",
        "| `predicted_pd_raw` / `predicted_pd` | 교정 전 / 교정 후 proxy PD |",
        "| `risk_grade_key` | `dim_risk_grade` [pd_lower, pd_upper) |",
        "| `is_past_due_history` | 과거 Home Credit 할부 중 1일 이상 지연 납부가 있었음 |",
        "| `is_severe_delinquency` | 과거 할부 최대 지연일수 ≥ severe_dpd_days(30) |",
        "| `*_band`, `*_band_sort` | SQL에서 정의한 구간 및 정렬키(“이력 없음”은 별도 구간) |",
        "| `reason_1..3` | 고객별 PD를 높인 방향의 SHAP 기여 상위 Feature (연관 설명, 인과 아님) |",
    ]
    out = settings.reports_dir / "data_dictionary.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_leakage_review(settings: Settings) -> Path:
    with connect(settings) as conn:
        dq = read_frame(conn, """
            SELECT layer, check_name, table_name, coalesce(column_name, '') AS column_name, status, observed_value,
                   expected_rule
              FROM audit.data_quality_result
             WHERE run_id IN (SELECT max(run_id) FROM audit.data_quality_result GROUP BY layer)
               AND check_name IN ('post_application_record', 'leakage_filter_removed_rows', 'split_id_intersection',
                                  'label_absent_in_test', 'train_test_id_overlap')
             ORDER BY layer, check_name, table_name""")
    metrics_path = settings.reports_dir / "model_metrics.json"
    leak = json.loads(metrics_path.read_text(encoding="utf-8")).get("leakage_check", {}) if metrics_path.exists() else {}
    feats = settings.features
    dq_rows = "\n".join(
        f"| {r.layer} | {r.check_name} | {r.table_name} | {r.column_name} | {r.status} | {r.observed_value:g} | "
        f"{r.expected_rule} |" for r in dq.itertuples())
    excl = ", ".join(f"`{c}`" for c in feats["exclude"])
    pats = ", ".join(f"`*{p}*`" for p in feats.get("exclude_patterns", []))
    sens = ", ".join(f"`{c}`" for c in feats.get("sensitivity_review", []))
    text = f"""# Leakage Review

{synthetic_banner(settings)}_검토 기준: 명세서 §3.3. 자동 검사 결과 표는 `credit-risk docs` 실행 시 갱신된다._

## 1. 결론
- 모델 입력은 **현재 신청의 의사결정 시점 이전에 기록된 정보**로 제한했다. 시점 확인이 불가능한 사후 정보는 제거하거나 NULL 처리했다.
- `TARGET` 및 그 파생값은 이름·패턴 규칙으로 Feature 목록에서 차단되고, 단일 Feature AUC 상한 검사로 재확인된다
  (최근 실행: 최대 단일 Feature AUC = **{leak.get('max_single_feature_auc', 'n/a')}**, `{leak.get('worst_feature', 'n/a')}`; 상한 0.95).
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
- 명시 제외: {excl}
- 패턴 제외: {pats}
- 민감도 검토 대상(포함하되 추적): {sens}

## 4. 분할·전처리 누수 통제
- `analytics.model_split`: stratified 70/15/15, seed 고정, 고객 ID 교집합 0 자동 검사(`split_id_intersection`).
- `application_test`는 내부 holdout으로 사용하지 않는다(무라벨 scoring 예시).
- LightGBM early stopping은 Train 내부 slice만 사용한다. Target encoding은 사용하지 않는다.
- 교정 방법 비교는 Validation 내부 CV, 최종 calibrator는 Validation 전체로 fit한다. Holdout에 fit하지 않는다.

## 5. 자동 검사 결과 (최근 실행)
| Layer | Check | Table | Column | Status | Observed | Rule |
|---|---|---|---|---|---:|---|
{dq_rows}

## 6. 남은 위험
- Kaggle 데이터에는 실제 calendar date가 없어 out-of-time 검증이 불가능하다.
- `EXT_SOURCE_*`는 생성 시점·방법이 공개되지 않은 외부 점수다. 신청 시점 제공 정보로 간주했으나, 외부 점수가 유사 레이블을 학습했을 가능성은 배제할 수 없다.
- Power BI에 노출되는 Train/Validation 예측은 in-sample 또는 선택용이며, 성능 판단은 Holdout 기준으로만 한다.
"""
    out = settings.reports_dir / "leakage_review.md"
    out.write_text(text, encoding="utf-8")
    return out
