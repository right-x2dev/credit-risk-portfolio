"""Generated documentation: model figures and the model card. Every number written here comes
from the training run's computed results; static text describes method, scope and limitations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .config import PROJECT_ROOT, Settings  # noqa: E402

NAVY, BLUE, AMBER, RED, GREY = "#1F3A5F", "#4F7CAC", "#E0A030", "#B8423A", "#8A94A6"


def _rel(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def is_synthetic(settings: Settings) -> bool:
    return (settings.data_dir / "SYNTHETIC_DATA_README.txt").exists()


def synthetic_banner(settings: Settings) -> str:
    if not is_synthetic(settings):
        return ""
    return ("> ⚠️ **합성(synthetic) 테스트 데이터 기반 결과 — 실제 Home Credit 분석 결과 아님.** "
            "실제 Kaggle 데이터를 `data/raw/`에 넣고 `./scripts/run_all.ps1`을 실행하면 이 문서가 재생성됩니다.\n\n")


def model_figures(settings: Settings, curves: pd.DataFrame, calib: pd.DataFrame, lift: pd.DataFrame,
                  metrics: pd.DataFrame, champion: str) -> dict[str, str]:
    fig_dir = settings.reports_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    colors = {"train": GREY, "validation": BLUE, "holdout": NAVY}

    def auc(split: str, metric: str) -> float:
        m = metrics[(metrics["split"] == split) & (metrics["score_type"] == "calibrated")
                    & (metrics["metric"] == metric) & (metrics["model_name"] == champion)]
        return float(m["value"].iloc[0]) if len(m) else float("nan")

    for curve, xl, yl, metric in (("roc", "False positive rate", "True positive rate", "roc_auc"),
                                  ("pr", "Recall", "Precision", "pr_auc")):
        fig, ax = plt.subplots(figsize=(5.5, 5))
        for split in ("validation", "holdout"):
            c = curves.query("curve==@curve and split==@split")
            ax.plot(c["x"], c["y"], color=colors[split], lw=2 if split == "holdout" else 1.2,
                    label=f"{split} ({metric.upper().replace('_', '-')} {auc(split, metric):.3f})")
        if curve == "roc":
            ax.plot([0, 1], [0, 1], ls="--", color=GREY, lw=0.8)
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        ax.set_title(f"{curve.upper()} curve — calibrated PD")
        ax.legend(loc="lower right" if curve == "roc" else "upper right")
        fig.tight_layout()
        paths[curve] = _rel(fig_dir / f"model_{curve}.png")
        fig.savefig(fig_dir / f"model_{curve}.png", dpi=130)
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), sharey=True)
    for ax, kind in zip(axes, ("raw", "calibrated"), strict=True):
        c = calib.query("split=='holdout' and score_type==@kind")
        lim = max(c["mean_pd"].max(), c["ci_high"].max()) * 1.05
        ax.plot([0, lim], [0, lim], ls="--", color=GREY, lw=0.8)
        ax.errorbar(c["mean_pd"], c["observed_rate"],
                    yerr=[c["observed_rate"] - c["ci_low"], c["ci_high"] - c["observed_rate"]],
                    fmt="o-", color=NAVY if kind == "calibrated" else AMBER, capsize=3)
        ax.set_title(f"Holdout calibration — {kind}")
        ax.set_xlabel("Mean predicted PD (decile)")
    axes[0].set_ylabel("Observed difficulty rate (95% Wilson CI)")
    fig.tight_layout()
    paths["calibration"] = _rel(fig_dir / "model_calibration.png")
    fig.savefig(fig_dir / "model_calibration.png", dpi=130)
    plt.close(fig)

    lt = lift.query("split=='holdout'")
    fig, ax1 = plt.subplots(figsize=(7, 4.5))
    ax1.bar(lt["decile"], lt["lift"], color=NAVY)
    ax1.axhline(1, ls="--", color=GREY, lw=0.8)
    ax1.set_xlabel("PD decile (1 = highest PD)")
    ax1.set_ylabel("Lift vs. average")
    ax2 = ax1.twinx()
    ax2.plot(lt["decile"], lt["cum_capture_rate"], color=AMBER, marker="o", label="cumulative capture (customers)")
    ax2.plot(lt["decile"], lt["cum_bad_exposure_capture"], color=RED, marker="s", label="cumulative capture (exposure)")
    ax2.set_ylim(0, 1.02)
    ax2.set_ylabel("Cumulative share of TARGET=1")
    ax2.legend(loc="center right")
    ax1.set_title("Holdout lift & gains")
    fig.tight_layout()
    paths["lift"] = _rel(fig_dir / "model_lift.png")
    fig.savefig(fig_dir / "model_lift.png", dpi=130)
    plt.close(fig)
    return paths


def _fmt(v: float, pct: bool = False, digits: int = 4) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{v:.{max(digits - 2, 1)}%}" if pct else f"{v:.{digits}f}"


def write_model_card(settings: Settings, s: dict, metrics: pd.DataFrame, seg: pd.DataFrame,
                     imp: pd.DataFrame, coef: pd.DataFrame) -> Path:
    champ = s["champion"]
    rows = []
    for model_name, kind in [("baseline_mean", "raw"), ("logistic_regression", "raw"), ("lightgbm", "raw"),
                             (champ, "calibrated")]:
        for split in ("validation", "holdout"):
            m = metrics.query("model_name==@model_name and score_type==@kind and split==@split")
            if m.empty:
                continue
            d = dict(zip(m["metric"], m["value"], strict=True))
            label = f"{model_name} ({kind})" + (" ★ champion" if model_name == champ and kind == "calibrated" else "")
            rows.append(f"| {label} | {split} | {_fmt(d['roc_auc'])} | {_fmt(d['pr_auc'])} | {_fmt(d['log_loss'])} | "
                        f"{_fmt(d['brier'])} | {_fmt(d['ks'])} | {_fmt(d['mean_pd'], True)} | {_fmt(d['observed_rate'], True)} |")
    ho = s["holdout_calibrated"]
    cal_rows = "\n".join(
        f"| {r['method']} | {r['cv_brier']:.5f} | {r['cv_log_loss']:.5f} | {r['cv_mean_pd']:.4f} | {r['observed_rate']:.4f} |"
        for r in s["calibration_comparison"])
    top = imp.head(15)
    imp_rows = "\n".join(f"| {r['rank']} | `{r['feature']}` | {r['mean_abs_shap']:.4f} | {r['direction']} |"
                         for _, r in top.iterrows())
    coef_rows = "\n".join(f"| `{r['term']}` | {r['coefficient']:+.4f} | {r['odds_ratio_per_sd']:.3f} |"
                          for _, r in coef.head(10).iterrows())
    seg_rows = "\n".join(
        f"| {r['group_attribute']} | {r['group_value']} | {r['customers']:,} | {r['observed_rate']:.2%} | "
        f"{r['mean_pd']:.2%} | {_fmt(r['roc_auc'], digits=3)} | {r['recall_at_threshold']:.2%} | "
        f"{'⚠️ 소표본' if r['small_sample_warning'] else ''} |"
        for _, r in seg.iterrows())
    fig = s.get("figures", {})
    text = f"""# Model Card — {s['model_version']}

{synthetic_banner(settings)}_자동 생성: `credit_risk.train` ({datetime.now():%Y-%m-%d %H:%M}). 모든 수치는 실행 결과에서 기입됨._

## 1. Intended use / Out-of-scope
- **용도:** 교육·포트폴리오 목적의 여신 포트폴리오 위험 모니터링. 고객별 **proxy PD**를 Exposure proxy 및 가정 LGD와 결합해
  Power BI에서 위험 집중도와 손실 시나리오를 탐색한다.
- **범위 외:** 실제 승인/거절 자동화, 가격 결정, IFRS 9 ECL·Basel 규제자본 산출, 개인에 대한 불이익 결정.
  성별·연령·가족상태 등은 진단용으로만 사용하며 승인 근거로 권고하지 않는다.

## 2. 데이터와 TARGET
- 원천: Kaggle *Home Credit Default Risk* `application_train.csv` 및 7개 관계형 이력 파일.
- `TARGET=1`은 대출 초기 기간 중 일정 일수 이상 연체가 관측되는 등 **상환 곤란이 관측된 고객**을 뜻한다.
  장기 생애부도·법적 파산·회계상 Stage 3와 동일하지 않다. 따라서 모델 출력은 **Home Credit TARGET을 예측한 proxy PD**이다.
- 모집단: application_train 고객 {sum(s['split_sizes'].values()):,}명. `application_test`는 무라벨 scoring 예시로만 사용.

## 3. Split
- Stratified Train/Validation/Holdout = 70/15/15, seed `{s['explain'].get('seed', settings.random_seed)}` → {s['split_sizes']}.
- 신뢰할 수 있는 달력 시점이 없어 시간 기반 분할이라고 주장하지 않는다. 고객 ID 교집합 0을 자동 검사한다.
- 모든 전처리(대치·클리핑·스케일링·인코딩)는 sklearn Pipeline 내부에서 **Train split에만 fit**한다.
  LightGBM early stopping은 Train 내부 10% slice로 수행(Validation·Holdout 미사용).

## 4. Features
- {s['n_features']}개 (numeric {s['n_numeric']}, categorical {s['n_categorical']}), 정책: `configs/features.yaml`.
- 자동 누수 검사: 제외 목록·label-like 이름 차단 + 단일 Feature AUC 상한(0.95).
  결과: 최대 단일 Feature AUC = {s['leakage_check'].get('max_single_feature_auc')} (`{s['leakage_check'].get('worst_feature')}`).
- 상세 검토: `reports/leakage_review.md`, 사전: `reports/data_dictionary.md`.

## 5. 모델과 선택
- Baseline 0: Train 평균 관측률 / Baseline 1: Logistic Regression (L2, C={settings.model['models']['logistic_regression']['C']}, 표준화)
  / Champion 후보: LightGBM (early-stopped trees = {s['best_iteration']}).
- 선택 기준: **Validation ROC-AUC** (Holdout은 선택에 사용하지 않음). Champion = **{champ}**
  (LogReg 대비 Validation AUC {s['champion_improvement_over_logreg']:+.4f}).
- 불균형 처리: class weight·SMOTE 미사용 — 확률 해석력을 유지하고, 순위 성능은 PR-AUC로 함께 보고.

| Model | Split | ROC-AUC | PR-AUC | Log Loss | Brier | KS | Mean PD | Observed rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

## 6. Calibration
Validation 내부 {settings.model['calibration']['cv_folds']}-fold CV로 비교, 선택 = **{s['calibration_method']}** (Holdout에는 fit하지 않음).
`predicted_pd_raw`(교정 전)와 `predicted_pd`(교정 후)를 모두 저장한다.

| Method | CV Brier | CV Log Loss | CV mean PD | Observed rate |
|---|---:|---:|---:|---:|
{cal_rows}

Holdout calibration-in-the-large (mean PD − observed): {_fmt(ho.get('calibration_in_the_large'))}.
![calibration]({'../' + fig.get('calibration', '')})

## 7. Threshold
- 정책: **review capacity** — Validation에서 PD 상위 {settings.model['threshold']['review_capacity_share']:.0%}를 수기 심사 대상으로 지정하는 컷오프를 고정.
- Selected threshold = **{s['threshold']:.4f}** → Holdout: precision {_fmt(ho.get('precision_at_threshold'), True)},
  recall {_fmt(ho.get('recall_at_threshold'), True)}, F1 {_fmt(ho.get('f1_at_threshold'))}, flagged {_fmt(ho.get('flagged_share_at_threshold'), True)},
  TARGET=1 Exposure capture {_fmt(ho.get('bad_exposure_capture_at_threshold'), True)}.
- Confusion matrix (Holdout): TP {int(ho.get('tp_at_threshold', 0)):,} · FP {int(ho.get('fp_at_threshold', 0)):,} ·
  FN {int(ho.get('fn_at_threshold', 0)):,} · TN {int(ho.get('tn_at_threshold', 0)):,}.
- Top-decile lift {_fmt(ho.get('top_decile_lift'), digits=3)}, top-decile capture {_fmt(ho.get('top_decile_capture'), True)}.

![roc]({'../' + fig.get('roc', '')}) ![pr]({'../' + fig.get('pr', '')})
![lift]({'../' + fig.get('lift', '')})

## 8. Explainability (association ≠ causation)
Champion SHAP (Holdout 표본 {s['explain']['shap_sample_size']:,}명):

| Rank | Feature | mean abs SHAP | Direction (descriptive) |
|---:|---|---:|---|
{imp_rows}

Logistic Regression 상위 계수 (표준화 입력 1 SD 기준 odds ratio):

| Term | Coefficient | Odds ratio / SD |
|---|---:|---:|
{coef_rows}

고객별 상위 3개 reason code(PD를 높인 방향의 기여)는 `analytics.reason_codes`에 저장된다. 기여도는 모델 내부의 연관 설명이며 인과 효과가 아니다.

## 9. Segment / fairness diagnostics (Holdout)
차별의 증명이 아니라 모델 차이를 진단하기 위한 표이다. 소표본(n < {settings.model['evaluation']['min_group_size']}) 그룹은 해석에 주의.

| Attribute | Group | n | Observed | Mean PD | AUC | Recall@thr | Note |
|---|---|---:|---:|---:|---:|---:|---|
{seg_rows}

## 10. Limitations & ethics
- TARGET·Exposure(AMT_CREDIT)·LGD(가정 {settings.project['risk']['base_lgd']:.0%})가 모두 proxy/가정이므로 Expected Loss는 **시나리오 지표**이며 충당금·규제자본이 아니다.
- 학습 표본은 Home Credit 신청자 표본이며 다른 고객군에 일반화하지 않는다. 승인된 고객만 결과가 관측되는 reject bias가 존재할 수 있다.
- 실제 운영 적용 전에는 법률·규제·공정성 검토와 독립 모델 검증이 필요하다.

## 11. Versioning
- model_version `{s['model_version']}` · feature_version `{settings.feature_version}` · artifact `{s['artifact']}`
- 재로딩 예측 최대 차이 {s['reload_max_abs_diff']:.2e} (허용 1e-9) · 학습 시간(초) {s['fit_seconds']}
- 재학습: `./scripts/train.ps1` (동일 데이터·seed·config → 동일 version 문자열).
"""
    out = settings.reports_dir / "model_card.md"
    out.write_text(text, encoding="utf-8")
    return out
