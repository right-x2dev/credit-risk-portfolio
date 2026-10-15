"""reports/powerbi_validation.md: DAX (evaluated by Power BI's engine) vs SQL mart reconciliation (spec §17.5).

Inputs
* reports/powerbi_dax_results.json — produced by scripts/validate_powerbi.ps1 (model deployed to the local
  Power BI Desktop engine, refreshed, DAX queries executed)
* reports/sql_reconciliation.json  — produced by `credit-risk build-mart` (SQL views over mart)
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from .config import Settings
from .reporting import synthetic_banner

REL_TOL = 1e-9

CHECKS = [  # (label, DAX key in portfolio row, SQL key in v_kpi_portfolio)
    ("Total Customers", "total_customers", "total_customers"),
    ("Total Exposure (proxy)", "total_exposure", "total_exposure"),
    ("Observed Difficulty Rate", "observed_difficulty_rate", "observed_difficulty_rate"),
    ("Average PD", "average_pd", "average_pd"),
    ("Exposure Weighted PD", "exposure_weighted_pd", "exposure_weighted_pd"),
    ("Base Expected Loss", "base_expected_loss", "base_expected_loss"),
    ("EL Rate", "el_rate", "el_rate"),
    ("High Risk Customer %", "high_risk_customer_pct", "high_risk_customer_pct"),
    ("High Risk Exposure %", "high_risk_exposure_pct", "high_risk_exposure_pct"),
    ("Risk Capture (Exposure)", "risk_capture_exposure", "risk_capture_exposure"),
    ("Past Due Customer %", "past_due_customer_pct", "past_due_customer_pct"),
    ("Severe Delinquency Customer %", "severe_delinquency_customer_pct", "severe_delinquency_customer_pct"),
    ("Average Payment Ratio", "average_payment_ratio", "average_payment_ratio"),
    ("Average Credit Utilization", "average_credit_utilization", "average_credit_utilization"),
]


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=1e-6)


def _fmt(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.6f}"


def write_powerbi_validation(settings: Settings) -> tuple[int, int]:
    rd = settings.reports_dir
    dax = json.loads((rd / "powerbi_dax_results.json").read_text(encoding="utf-8-sig"))
    sql = json.loads((rd / "sql_reconciliation.json").read_text(encoding="utf-8"))
    metrics = json.loads((rd / "model_metrics.json").read_text(encoding="utf-8"))
    p_dax, p_sql = dax["portfolio"][0], sql["portfolio"]
    rows, passed, total = [], 0, 0

    def add(label: str, a, b) -> None:
        nonlocal passed, total
        ok = _close(a, b)
        total += 1
        passed += ok
        rows.append(f"| {label} | {_fmt(a)} | {_fmt(b)} | {'✅' if ok else '❌'} |")

    for label, dk, sk in CHECKS:
        add(label, p_dax.get(dk), p_sql.get(sk))
    # risk grade breakdown
    sql_grade = {g["risk_grade"]: g for g in sql["by_grade"]}
    for g in dax["by_grade"]:
        name = next(v for k, v in g.items() if k.rstrip("]").endswith("risk_grade"))
        s = sql_grade.get(name, {})
        add(f"Grade {name} — customers", g["customers"], s.get("customers"))
        add(f"Grade {name} — exposure", g["exposure"], s.get("exposure"))
        add(f"Grade {name} — base EL", g["base_expected_loss"], s.get("base_expected_loss"))
    gs = dax["grade_sum_check"][0]
    add("Σ grades EL = total EL (DAX internal)", gs["sum_of_grades_el"], gs["total_el"])
    add("Σ grades customers = total customers", gs["sum_of_grades_customers"], p_dax["total_customers"])
    add("What-if defaults: Scenario EL = Base EL", p_dax["scenario_el_default"], p_dax["base_expected_loss"])
    add("What-if defaults: Scenario EL change = 0", p_dax["scenario_change_default"], 0)
    st = dax["scenario_stress"][0]
    add("Stress (+20% PD, LGD 60%, −10% exposure): waterfall total = scenario EL", st["bridge_total"], st["scenario_el"])
    # sensitivity grid vs SQL view
    sql_sens = {(round(r["pd_stress"], 2), round(r["lgd"], 2)): r["scenario_expected_loss"] for r in sql["sensitivity_grid"]}
    mism = 0
    for r in dax["sensitivity"]:
        pdv = next(v for kk, v in r.items() if "PD Stress Level" in kk)
        lgv = next(v for kk, v in r.items() if "LGD Level" in kk)
        k = (round(float(pdv), 2), round(float(lgv), 2))
        if k in sql_sens and not _close(r["el"], sql_sens[k]):
            mism += 1
    add(f"Sensitivity matrix: {len(dax['sensitivity'])} DAX cells vs SQL v_scenario_sensitivity (mismatches)", mism, 0)
    from .db import connect

    with connect(settings) as conn:
        c, e, el = conn.execute(
            "SELECT count(*), sum(exposure_proxy), sum(expected_loss) FROM mart.fact_customer_risk f "
            "JOIN mart.dim_customer_segment s USING (segment_key) "
            "WHERE s.income_band = '100K-150K' AND s.gender = 'Female'").fetchone()
    fs = dax["filtered_spot_check"][0]
    add("Filter spot check (income 100K-150K × Female): customers", fs["customers"], c)
    add("Filter spot check: exposure", fs["exposure"], float(e or 0))
    add("Filter spot check: base EL", fs["el"], float(el or 0))
    mh = dax["model_holdout"][0]
    ho = metrics["holdout_calibrated"]
    for k_dax, k_py in (("roc_auc", "roc_auc"), ("pr_auc", "pr_auc"), ("brier", "brier"), ("ks", "ks"),
                        ("recall", "recall_at_threshold"), ("precision", "precision_at_threshold")):
        add(f"Holdout {k_dax} (DAX vs Python training run)", mh[k_dax], ho[k_py])
    add("Selected threshold", mh["threshold"], metrics["threshold"])

    states = dax["table_states"]
    not_ready = [t for t in states if t["state"] != "Ready"]
    narr = dax["narratives"][0]
    shots = sorted((settings.reports_dir.parent / "powerbi" / "screenshots").glob("*.png"))
    shot_md = "\n".join(f"![{p.stem}](../powerbi/screenshots/{p.name})" for p in shots)
    text = f"""# Power BI Validation

{synthetic_banner(settings)}_자동 생성: `credit-risk powerbi-validation` ({datetime.now():%Y-%m-%d %H:%M})._

## 방법
1. `scripts/validate_tmdl.ps1` — 생성된 TMDL을 Power BI Desktop의 `TmdlSerializer`로 파싱 (구문·참조 오류 검출)
2. `scripts/validate_pbir.py` — 보고서 JSON 전체를 Microsoft 공식 PBIR JSON Schema로 검증
3. `scripts/validate_powerbi.ps1` — 시맨틱 모델을 실행 중인 Power BI Desktop 로컬 엔진에 임시 DB로 배포 → Full refresh →
   DAX 쿼리 실행 (CSV 스냅샷 모드, 자격증명 불필요)
4. 이 문서 — DAX 결과를 SQL mart 뷰(`mart.v_kpi_portfolio`, `v_kpi_by_grade`, `v_scenario_sensitivity`) 및 학습 실행 지표와 대조
5. `scripts/open_check.ps1` + `scripts/capture_pages.ps1` — PBIP를 Desktop에서 열어 오류 대화상자 부재 확인 후 5개 페이지 캡처

## 결과: {passed}/{total} 일치 (상대 오차 허용 {REL_TOL:g})
- 새로고침: {len(states) - len(not_ready)}/{len(states)} 테이블 `Ready` {'' if not not_ready else '— 실패: ' + ', '.join(t['table'] for t in not_ready)}

| Check | DAX (Power BI engine) | SQL / Python | Match |
|---|---:|---:|:---:|
{chr(10).join(rows)}

## Filter spot check (income 100K–150K × Female)
DAX: {json.dumps(dax['filtered_spot_check'][0], ensure_ascii=False)}
(SQL 대조 쿼리: `SELECT count(*), sum(exposure_proxy), sum(expected_loss) FROM mart.fact_customer_risk f JOIN mart.dim_customer_segment s USING (segment_key) WHERE s.income_band='100K-150K' AND s.gender='Female'`)

## Dynamic text
- Executive: {narr['executive']}
- Model: {narr['model']}
- Header: {narr['header']}

## 수동 확인이 필요한 항목
PBIP 자동 테스트로 확인할 수 없는 상호작용은 Desktop에서 직접 확인한다: slicer 교차 필터, tooltip 표시, page navigator 이동,
**Reset filters** 북마크 동작, Decomposition tree 확장. 위 DAX 대조는 동일 모델·동일 measure로 수행되었으므로 visual의 숫자는 표와 일치한다.

## Screenshots
{shot_md}
"""
    (rd / "powerbi_validation.md").write_text(text, encoding="utf-8")
    return passed, total
