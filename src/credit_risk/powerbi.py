"""Power BI Project (PBIP) generator: TMDL semantic model + PBIR report (5 pages).

Why generate instead of hand-building a .pbix?
* the model, DAX and layout are reviewable text in Git (spec §21.5: "PBIX만 남기지 않음")
* the table/column list and types are read from the live `mart` schema, so the report never drifts
  from the SQL star schema
Open `powerbi/Credit_Risk_Portfolio.pbip` in Power BI Desktop, refresh, then *Save as* .pbix if a
binary file is needed.

Business rules stay in SQL; DAX only aggregates and applies What-if multipliers (spec §21.5).
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .db import connect

log = logging.getLogger(__name__)

NAME = "Credit_Risk_Portfolio"
PBI_DIR = PROJECT_ROOT / "powerbi"
MEASURES = "00_Measures"
NAVY, BLUE, LIGHT, GREY, AMBER, RED, BG = "#1F3A5F", "#4F7CAC", "#DCE6F0", "#5B6B7F", "#E0A030", "#B8423A", "#F5F7FA"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"

MART_TABLES = [
    "dim_risk_grade", "dim_model_version", "dim_split", "dim_customer_segment", "fact_customer_risk",
    "fact_scenario_base", "fact_model_evaluation", "fact_model_curve", "fact_calibration", "fact_lift",
    "fact_feature_importance", "fact_segment_performance", "fact_confusion_matrix", "v_report_metadata",
]
TABLE_ALIAS = {"v_report_metadata": "report_metadata"}

PG_TO_TMDL = {
    "bigint": ("int64", "Int64.Type"), "integer": ("int64", "Int64.Type"), "smallint": ("int64", "Int64.Type"),
    "double precision": ("double", "type number"), "numeric": ("double", "type number"), "real": ("double", "type number"),
    "text": ("string", "type text"), "character varying": ("string", "type text"),
    "boolean": ("boolean", "type logical"), "date": ("dateTime", "type date"),
    "timestamp with time zone": ("dateTime", "type datetime"), "timestamp without time zone": ("dateTime", "type datetime"),
}

# sort-by-column pairs (column -> sort column) applied wherever both exist
SORT_BY = {
    "risk_grade": "sort_key", "risk_grade_label": "sort_key", "age_band": "age_band_sort", "income_band": "income_band_sort",
    "max_dpd_band": "max_dpd_band_sort", "payment_ratio_band": "payment_ratio_band_sort",
    "utilization_band": "utilization_band_sort", "bureau_active_band": "bureau_active_band_sort",
    "bureau_debt_ratio_band": "bureau_debt_ratio_band_sort", "prev_outcome_group": "prev_outcome_group_sort",
    "split_label": "sort_key", "actual": "actual_sort", "predicted": "predicted_sort",
}
CURRENCY = "#,0"
PCT1, PCT2 = "0.0%", "0.00%"


def _uid() -> str:
    return str(uuid.uuid4())


def q(name: str) -> str:
    """TMDL/DAX object name quoting."""
    return name if name.replace("_", "").isalnum() and not name[0].isdigit() else "'" + name.replace("'", "''") + "'"


# =============================================================================================== semantic model
@dataclass
class Column:
    name: str
    data_type: str
    m_type: str
    hidden: bool = False
    fmt: str | None = None
    sort_by: str | None = None
    summarize: str = "none"


@dataclass
class Measure:
    name: str
    expr: str
    fmt: str | None = None
    folder: str | None = None
    description: str | None = None


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    source_table: str | None = None          # mart table/view -> M partition
    calculated: str | None = None            # DAX calculated table
    measures: list[Measure] = field(default_factory=list)
    hidden: bool = False


def _column_format(table: str, col: str, dtype: str) -> tuple[str | None, bool]:
    hidden = col.endswith("_key") or col.endswith("_sort") or col in {"sort_key", "point_order", "sk_id_curr"}
    if dtype not in ("int64", "double"):
        return None, hidden
    if col in {"exposure_proxy", "expected_loss", "amt_income_total", "amt_annuity", "amt_goods_price", "exposure",
               "bad_exposure", "base_expected_loss", "pd_x_exposure"}:
        return CURRENCY, hidden
    if any(t in col for t in ("pd", "rate", "ratio", "share", "capture", "utilization", "lgd", "threshold")) \
            and "pd_bucket" not in col and "pd_bin" not in col:
        return PCT2, hidden
    if dtype == "double":
        return "0.0000", hidden
    return "0", hidden


def introspect_mart(settings: Settings) -> list[Table]:
    tables = []
    with connect(settings) as conn:
        for t in MART_TABLES:
            rows = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='mart' "
                "AND table_name=%s ORDER BY ordinal_position", (t,)).fetchall()
            if not rows:
                raise RuntimeError(f"mart.{t} not found — run `credit-risk build-mart` first")
            cols = []
            names = {r[0] for r in rows}
            for name, pg_type in rows:
                dtype, mtype = PG_TO_TMDL.get(pg_type, ("string", "type text"))
                fmt, hidden = _column_format(t, name, dtype)
                sort_by = SORT_BY.get(name) if SORT_BY.get(name) in names else None
                cols.append(Column(name, dtype, mtype, hidden=hidden, fmt=fmt, sort_by=sort_by))
            tables.append(Table(TABLE_ALIAS.get(t, t), cols, source_table=t))
    return tables


def m_partition(t: Table) -> str:
    bools = [c.name for c in t.columns if c.data_type == "boolean"]
    types = ", ".join(f'{{"{c.name}", {c.m_type}}}' for c in t.columns)
    csv_fix = ""
    if bools:
        repl = ", ".join(f'{{"{b}", each if _ = null then null else Text.Lower(_) = "true" or _ = "t"}}' for b in bools)
        csv_fix = f",\n    #\"Parsed booleans\" = if DataSource = \"PostgreSQL\" then Source else Table.TransformColumns(Source, {{{repl}}})"
    last = '#"Parsed booleans"' if bools else "Source"
    return f"""let
    Source =
        if DataSource = "PostgreSQL" then
            PostgreSQL.Database(PgServer, PgDatabase){{[Schema = "mart", Item = "{t.source_table}"]}}[Data]
        else
            Table.PromoteHeaders(
                Csv.Document(File.Contents(CsvFolder & "{t.source_table}.csv"),
                    [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
                [PromoteAllScalars = true]){csv_fix},
    #"Typed columns" = Table.TransformColumnTypes({last}, {{{types}}}, "en-US")
in
    #"Typed columns\""""


def _indent(text: str, n: int) -> str:
    pad = "\t" * n
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def table_tmdl(t: Table) -> str:
    out = [f"table {q(t.name)}", f"\tlineageTag: {_uid()}"]
    if t.hidden:
        out.append("\tisHidden")
    out.append("")
    for m in t.measures:
        if m.description:  # TMDL descriptions are /// doc comments
            out.append(f"\t/// {m.description}")
        out.append(f"\tmeasure {q(m.name)} = {m.expr}")
        if m.fmt:
            out.append(f"\t\tformatString: {m.fmt}")
        if m.folder:
            out.append(f"\t\tdisplayFolder: {m.folder}")
        out.append(f"\t\tlineageTag: {_uid()}")
        out.append("")
    for c in t.columns:
        out.append(f"\tcolumn {q(c.name)}")
        out.append(f"\t\tdataType: {c.data_type}")
        if c.fmt:
            out.append(f"\t\tformatString: {c.fmt}")
        if c.hidden:
            out.append("\t\tisHidden")
        out.append(f"\t\tlineageTag: {_uid()}")
        out.append(f"\t\tsummarizeBy: {c.summarize}")
        if t.calculated:
            src = "[Value]" if c.name.endswith(" Value") else f"[{c.name}]"
        else:
            src = c.name
        out.append(f"\t\tsourceColumn: {src}")
        if c.sort_by:
            out.append(f"\t\tsortByColumn: {q(c.sort_by)}")
        out.append("")
        out.append("\t\tannotation SummarizationSetBy = User")
        out.append("")
    if t.calculated:
        out.append(f"\tpartition {q(t.name)} = calculated")
        out.append("\t\tmode: import")
        out.append(f"\t\tsource = {t.calculated}")
    elif t.source_table:
        out.append(f"\tpartition {q(t.name)} = m")
        out.append("\t\tmode: import")
        out.append("\t\tsource =")
        out.append(_indent(m_partition(t), 4))
    else:  # measure-only table
        out.append(f"\tpartition {q(t.name)} = m")
        out.append("\t\tmode: import")
        out.append("\t\tsource =")
        out.append(_indent('let\n    Source = #table(type table [Placeholder = text], {}),\n'
                           '    #"Removed placeholder" = Table.RemoveColumns(Source, {"Placeholder"})\n'
                           'in\n    #"Removed placeholder"', 4))
    out.append("")
    out.append("\tannotation PBI_ResultType = Table")
    return "\n".join(out) + "\n"


def whatif_table(name: str, col: str, start: float, end: float, step: float, fmt: str) -> Table:
    return Table(name, [Column(col, "double", "type number", fmt=fmt)],
                 calculated=f"GENERATESERIES({start}, {end}, {step})")


F = "fact_customer_risk"
G = "dim_risk_grade"
E = "fact_model_evaluation"


def _model_metric(metric: str) -> str:
    return (f"IF(HASONEVALUE(dim_split[dataset_split]), CALCULATE(MAX({E}[value]), {E}[metric] = \"{metric}\", "
            f"{E}[is_champion_row] = TRUE()))")


def build_measures() -> list[Measure]:
    hi = f'KEEPFILTERS({G}[risk_group] IN {{"High", "Critical"}})'
    m = [
        # ---- portfolio (Page 1/2)
        Measure("Total Customers", f"DISTINCTCOUNT({F}[sk_id_curr])", "#,0", "Portfolio"),
        Measure("Total Exposure", f"SUM({F}[exposure_proxy])", CURRENCY, "Portfolio",
                "Exposure proxy = AMT_CREDIT (not an observed EAD)"),
        Measure("Observed Difficulty Customers", f"CALCULATE([Total Customers], {F}[target] = 1)", "#,0", "Portfolio"),
        Measure("Observed Difficulty Rate", "DIVIDE([Observed Difficulty Customers], [Total Customers])", PCT2, "Portfolio",
                "Share of customers with TARGET = 1 (observed repayment difficulty, not a lifetime default rate)"),
        Measure("Average PD", f"AVERAGE({F}[predicted_pd])", PCT2, "Portfolio",
                "Simple average of calibrated proxy PD (predicts Home Credit TARGET)"),
        Measure("Exposure Weighted PD",
                f"DIVIDE(SUMX({F}, {F}[predicted_pd] * {F}[exposure_proxy]), [Total Exposure])", PCT2, "Portfolio"),
        Measure("Base Expected Loss", f"SUM({F}[expected_loss])", CURRENCY, "Portfolio",
                "Expected Loss (Scenario) = PD x AMT_CREDIT x assumed LGD; not a provision or regulatory figure"),
        Measure("EL Rate", "DIVIDE([Base Expected Loss], [Total Exposure])", PCT2, "Portfolio"),
        Measure("High Risk Customers", f"CALCULATE([Total Customers], {hi})", "#,0", "Portfolio"),
        Measure("High Risk Customer %", "DIVIDE([High Risk Customers], [Total Customers])", PCT1, "Portfolio"),
        Measure("High Risk Exposure", f"CALCULATE([Total Exposure], {hi})", CURRENCY, "Portfolio"),
        Measure("High Risk Exposure %", "DIVIDE([High Risk Exposure], [Total Exposure])", PCT1, "Portfolio"),
        Measure("High Risk Expected Loss", f"CALCULATE([Base Expected Loss], {hi})", CURRENCY, "Portfolio"),
        Measure("Risk Capture (Exposure)",
                f"DIVIDE(CALCULATE([Total Exposure], {hi}, {F}[target] = 1), CALCULATE([Total Exposure], {F}[target] = 1))",
                PCT1, "Portfolio", "Share of TARGET=1 exposure that sits in High/Critical grades"),
        Measure("Customer Share %", f"DIVIDE([Total Customers], CALCULATE([Total Customers], ALLSELECTED({G})))", PCT1,
                "Portfolio"),
        Measure("Exposure Share %", f"DIVIDE([Total Exposure], CALCULATE([Total Exposure], ALLSELECTED({G})))", PCT1,
                "Portfolio"),
        Measure("EL Share of Portfolio %",
                f"DIVIDE([Base Expected Loss], CALCULATE([Base Expected Loss], ALLSELECTED({F})))", PCT1, "Portfolio"),
        Measure("Exposure Share of Portfolio %",
                f"DIVIDE([Total Exposure], CALCULATE([Total Exposure], ALLSELECTED({F})))", PCT1, "Portfolio"),
        Measure("Average Exposure", "DIVIDE([Total Exposure], [Total Customers])", CURRENCY, "Portfolio"),
        Measure("Executive Narrative",
                '"High/Critical grades hold " & FORMAT([High Risk Customer %], "0.0%") & " of customers but "'
                ' & FORMAT([High Risk Exposure %], "0.0%") & " of exposure and " '
                '& FORMAT(DIVIDE([High Risk Expected Loss], [Base Expected Loss]), "0.0%") & " of expected loss (scenario). '
                'Exposure-weighted PD is " & FORMAT([Exposure Weighted PD], "0.00%") & " vs. an observed difficulty rate of " '
                '& FORMAT([Observed Difficulty Rate], "0.00%") & "."', None, "Narrative"),
        # ---- repayment (Page 3)
        Measure("Past Due Customer %", f"AVERAGE({F}[is_past_due_history])", PCT1, "Repayment",
                "Customers with >= 1 late Home Credit instalment before this application (customers with history only)"),
        Measure("Severe Delinquency Customer %", f"AVERAGE({F}[is_severe_delinquency])", PCT1, "Repayment",
                "Max instalment DPD >= 30 days before this application"),
        Measure("Average Max DPD", f"AVERAGE({F}[inst_dpd_max])", "0.0", "Repayment"),
        Measure("Median Max DPD", f"MEDIAN({F}[inst_dpd_max])", "0.0", "Repayment"),
        Measure("Average Payment Ratio", f"AVERAGE({F}[inst_payment_ratio_mean])", PCT1, "Repayment"),
        Measure("Average Credit Utilization", f"AVERAGE({F}[cc_utilization_mean])", PCT1, "Repayment"),
        # ---- model (Page 4)
        Measure("ROC-AUC", _model_metric("roc_auc"), "0.000", "Model"),
        Measure("PR-AUC", _model_metric("pr_auc"), "0.000", "Model"),
        Measure("Brier Score", _model_metric("brier"), "0.0000", "Model"),
        Measure("Log Loss", _model_metric("log_loss"), "0.0000", "Model"),
        Measure("KS", _model_metric("ks"), "0.000", "Model"),
        Measure("Recall at Threshold", _model_metric("recall_at_threshold"), PCT1, "Model"),
        Measure("Precision at Threshold", _model_metric("precision_at_threshold"), PCT1, "Model"),
        Measure("Calibration in the Large", _model_metric("calibration_in_the_large"), "+0.00%;-0.00%", "Model"),
        Measure("Selected Threshold", "MAX(dim_model_version[selected_threshold])", PCT2, "Model"),
        Measure("ROC True Positive Rate",
                'CALCULATE(AVERAGE(fact_model_curve[y]), fact_model_curve[curve] = "roc")', "0.000", "Model"),
        Measure("Random Classifier", 'CALCULATE(AVERAGE(fact_model_curve[x]), fact_model_curve[curve] = "roc")',
                "0.000", "Model"),
        Measure("PR Precision", 'CALCULATE(AVERAGE(fact_model_curve[y]), fact_model_curve[curve] = "pr")', "0.000", "Model"),
        Measure("Calibration Mean PD", 'CALCULATE(AVERAGE(fact_calibration[mean_pd]), fact_calibration[score_type] = "calibrated")',
                PCT2, "Model"),
        Measure("Calibration Observed Rate",
                'CALCULATE(AVERAGE(fact_calibration[observed_rate]), fact_calibration[score_type] = "calibrated")', PCT2, "Model"),
        Measure("Raw Score Mean PD", 'CALCULATE(AVERAGE(fact_calibration[mean_pd]), fact_calibration[score_type] = "raw")',
                PCT2, "Model"),
        Measure("Calibration Bin Customers",
                'CALCULATE(SUM(fact_calibration[customers]), fact_calibration[score_type] = "calibrated")', "#,0", "Model"),
        Measure("Confusion Count", "SUM(fact_confusion_matrix[customers])", "#,0", "Model"),
        Measure("Decile Lift", "AVERAGE(fact_lift[lift])", "0.00", "Model"),
        Measure("Cumulative Capture", "AVERAGE(fact_lift[cum_capture_rate])", PCT1, "Model"),
        Measure("Cumulative Exposure Capture", "AVERAGE(fact_lift[cum_bad_exposure_capture])", PCT1, "Model"),
        Measure("Top 15 Mean abs SHAP",
                "IF(MAX(fact_feature_importance[importance_rank]) <= 15, SUM(fact_feature_importance[mean_abs_shap]))",
                "0.0000", "Model"),
        Measure("Model Summary Narrative",
                'SELECTEDVALUE(dim_split[split_label], "Select one split") & ": AUC " & FORMAT([ROC-AUC], "0.000") '
                '& " · PR-AUC " & FORMAT([PR-AUC], "0.000") & " · KS " & FORMAT([KS], "0.000") '
                '& " · mean PD − observed " & FORMAT([Calibration in the Large], "+0.00%;-0.00%") '
                '& " · @thr " & FORMAT([Selected Threshold], "0.0%") & " recall " & FORMAT([Recall at Threshold], "0.0%") '
                '& ", precision " & FORMAT([Precision at Threshold], "0.0%") & " (proxy PD; associations, not causes)"',
                None, "Narrative"),
        # ---- scenario (Page 5): relative multipliers (20% = x1.2), stressed PD capped at 1
        Measure("PD Stress Multiplier", "1 + SELECTEDVALUE('PD Stress'[PD Stress Value], 0)", "0.00x", "Scenario",
                "Relative multiplier: +20% means PD x 1.2 (not +20 percentage points)"),
        Measure("Selected LGD", "SELECTEDVALUE('LGD Assumption'[LGD Assumption Value], 0.45)", PCT1, "Scenario",
                "Assumed LGD (no observed recoveries in the data)"),
        Measure("Exposure Multiplier", "1 + SELECTEDVALUE('Exposure Change'[Exposure Change Value], 0)", "0.00x", "Scenario"),
        Measure("Scenario Expected Loss",
                f"VAR s = [PD Stress Multiplier] VAR l = [Selected LGD] VAR x = [Exposure Multiplier] RETURN "
                f"SUMX({F}, MIN(1, {F}[predicted_pd] * s) * {F}[exposure_proxy] * x * l)", CURRENCY, "Scenario"),
        Measure("Scenario EL Change", "[Scenario Expected Loss] - [Base Expected Loss]", CURRENCY, "Scenario"),
        Measure("Scenario EL Change %", "DIVIDE([Scenario EL Change], [Base Expected Loss])", "+0.0%;-0.0%;0.0%", "Scenario"),
        Measure("Scenario Exposure", "[Total Exposure] * [Exposure Multiplier]", CURRENCY, "Scenario"),
        Measure("Scenario EL Rate", "DIVIDE([Scenario Expected Loss], [Scenario Exposure])", PCT2, "Scenario"),
        Measure("Scenario EL Share %",
                f"DIVIDE([Scenario Expected Loss], CALCULATE([Scenario Expected Loss], ALLSELECTED({F})))", PCT1, "Scenario"),
        Measure("EL Bridge Value",
                f"VAR s = [PD Stress Multiplier] VAR l = [Selected LGD] VAR x = [Exposure Multiplier] "
                f"VAR el0 = [Base Expected Loss] "
                f"VAR el1 = SUMX({F}, MIN(1, {F}[predicted_pd] * s) * {F}[exposure_proxy] * {F}[base_lgd]) "
                f"VAR el2 = SUMX({F}, MIN(1, {F}[predicted_pd] * s) * {F}[exposure_proxy] * l) "
                f"VAR el3 = el2 * x "
                f"RETURN SWITCH(SELECTEDVALUE('EL Bridge'[Step Order]), 1, el0, 2, el1 - el0, 3, el2 - el1, 4, el3 - el2)",
                CURRENCY, "Scenario"),
        Measure("Sensitivity EL",
                f"VAR s = SELECTEDVALUE('Sensitivity PD'[PD Stress Level], 0) "
                f"VAR l = SELECTEDVALUE('Sensitivity LGD'[LGD Level], 0.45) "
                f"RETURN SUMX({F}, MIN(1, {F}[predicted_pd] * (1 + s)) * {F}[exposure_proxy] * l)", CURRENCY, "Scenario"),
        # ---- metadata shown on every page
        Measure("Header Info",
                'IF(SELECTEDVALUE(report_metadata[is_synthetic], FALSE()), "SYNTHETIC TEST DATA (not Home Credit) · ", "") & "Population " & FORMAT(MAX(report_metadata[population_customers]), "#,0") & " applications · Model "'
                ' & MAX(report_metadata[model_version]) & " · Refreshed " '
                '& FORMAT(MAX(report_metadata[last_refreshed]), "yyyy-mm-dd hh:nn") & " UTC"', None, "Metadata"),
        Measure("Grade Color", f"SELECTEDVALUE({G}[color_hex], \"{GREY}\")", None, "Metadata"),
    ]
    return m


def build_semantic_model(settings: Settings) -> list[Table]:
    tables = introspect_mart(settings)
    for t in tables:  # fact columns that are meant to be summed must not be summarize=none for implicit visuals
        for c in t.columns:
            if c.name in {"customers", "positives"} and t.name.startswith("fact_"):
                c.summarize = "sum"
    tables.append(Table(MEASURES, measures=build_measures()))
    tables += [
        whatif_table("PD Stress", "PD Stress Value", -0.2, 1, 0.05, "+0%;-0%;0%"),
        whatif_table("LGD Assumption", "LGD Assumption Value", 0.1, 0.9, 0.05, "0%"),
        whatif_table("Exposure Change", "Exposure Change Value", -0.3, 0.3, 0.05, "+0%;-0%;0%"),
        Table("Sensitivity PD", [Column("PD Stress Level", "double", "type number", fmt="+0%;-0%;0%")],
              calculated="SELECTCOLUMNS(GENERATESERIES(-0.2, 1, 0.2), \"PD Stress Level\", [Value])"),
        Table("Sensitivity LGD", [Column("LGD Level", "double", "type number", fmt="0%")],
              calculated="SELECTCOLUMNS(GENERATESERIES(0.1, 0.9, 0.1), \"LGD Level\", [Value])"),
        Table("EL Bridge", [Column("Step", "string", "type text", sort_by="Step Order"),
                            Column("Step Order", "int64", "Int64.Type", hidden=True)],
              calculated='DATATABLE("Step", STRING, "Step Order", INTEGER, {{"1 Base EL", 1}, {"2 PD stress", 2}, '
                         '{"3 LGD change", 3}, {"4 Exposure change", 4}})'),
    ]
    return tables


RELATIONSHIPS = [
    ("dim_risk_grade", "risk_grade_key", "fact_customer_risk", "risk_grade_key"),
    ("dim_customer_segment", "segment_key", "fact_customer_risk", "segment_key"),
    ("dim_model_version", "model_version_key", "fact_customer_risk", "model_version_key"),
    ("dim_split", "dataset_split", "fact_customer_risk", "dataset_split"),
    ("dim_risk_grade", "risk_grade_key", "fact_scenario_base", "risk_grade_key"),
    ("dim_customer_segment", "segment_key", "fact_scenario_base", "segment_key"),
] + [("dim_model_version", "model_version_key", f, "model_version_key") for f in (
    "fact_model_evaluation", "fact_model_curve", "fact_calibration", "fact_lift", "fact_feature_importance",
    "fact_segment_performance", "fact_confusion_matrix")] + [("dim_split", "dataset_split", f, "dataset_split") for f in (
        "fact_model_evaluation", "fact_model_curve", "fact_calibration", "fact_lift", "fact_segment_performance",
        "fact_confusion_matrix")]


def write_semantic_model(settings: Settings, root: Path, tables: list[Table], data_source: str) -> None:
    sm = root / f"{NAME}.SemanticModel"
    d = sm / "definition"
    (d / "tables").mkdir(parents=True, exist_ok=True)
    _write(sm / ".platform", json.dumps({
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": "4d1c5b0e-6a59-4b8f-9a62-2a7c3c1f0a11"}}, indent=2))
    _write(sm / "definition.pbism", json.dumps({
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}}, indent=2))
    _write(sm / ".pbi" / "localSettings.json", json.dumps({
        "$schema": f"{SCHEMA}/item/semanticModel/localSettings/1.2.0/schema.json"}, indent=2))
    _write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    order = json.dumps([t.name for t in tables])
    model = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull",
             "", "\tannotation __PBI_TimeIntelligenceEnabled = 0", "", f"\tannotation PBI_QueryOrder = {order}", ""]
    model += [f"ref table {q(t.name)}" for t in tables]
    model += ["", "ref expression DataSource", "ref expression CsvFolder", "ref expression PgServer",
              "ref expression PgDatabase"]
    _write(d / "model.tmdl", "\n".join(model) + "\n")
    csv_folder = str(settings.export_dir.resolve()).rstrip("\\/") + "\\"
    pg_server = f"{settings.pg_host}:{settings.pg_port}"

    def param(name: str, value: str, desc: str, allowed: list[str] | None = None) -> str:
        meta = 'IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true'
        if allowed:
            meta += ", List={" + ", ".join(f'"{a}"' for a in allowed) + "}, DefaultValue=\"" + value + "\""
        return (f"/// {desc}\nexpression {name} = \"{value}\" meta [{meta}]\n\tlineageTag: {_uid()}\n"
                f"\n\tannotation PBI_ResultType = Text\n")

    _write(d / "expressions.tmdl", "\n".join([
        param("DataSource", data_source, "PostgreSQL = live mart schema (Import); CSV = exported mart snapshot",
              ["PostgreSQL", "CSV"]),
        param("CsvFolder", csv_folder.replace("\\", "\\\\"), "Folder with the mart CSV snapshot (credit-risk export-bi)"),
        param("PgServer", pg_server, "PostgreSQL host:port"),
        param("PgDatabase", settings.pg_db, "PostgreSQL database"),
    ]))
    rels = []
    for i, (dt, dc, ft, fc) in enumerate(RELATIONSHIPS):
        rels += [f"relationship {_uid()}", f"\tfromColumn: {q(ft)}.{q(fc)}", f"\ttoColumn: {q(dt)}.{q(dc)}", ""]
        _ = i
    _write(d / "relationships.tmdl", "\n".join(rels))
    for t in tables:
        _write(d / "tables" / f"{t.name}.tmdl", table_tmdl(t))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:   # no BOM — Power BI rejects BOM files
        fh.write(text)


# =============================================================================================== report (PBIR)
def lit(v: Any) -> dict:
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def color(hex_: str) -> dict:
    return {"solid": {"color": lit(hex_)}}


def col(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def meas(prop: str, entity: str = MEASURES) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(f: dict, active: bool = False, display: str | None = None) -> dict:
    inner = f.get("Column") or f.get("Measure")
    ent, prop = inner["Expression"]["SourceRef"]["Entity"], inner["Property"]
    p = {"field": f, "queryRef": f"{ent}.{prop}", "nativeQueryRef": display or prop}
    if display:
        p["displayName"] = display
    if active:
        p["active"] = True
    return p


@dataclass
class Visual:
    name: str
    vtype: str
    x: int
    y: int
    w: int
    h: int
    roles: dict[str, list[dict]] = field(default_factory=dict)
    title: str | None = None
    objects: dict = field(default_factory=dict)
    container: dict = field(default_factory=dict)
    sort: list | None = None
    filters: list | None = None
    z: int = 1000

    def to_json(self, tab: int) -> dict:
        vis: dict[str, Any] = {"visualType": self.vtype}
        if self.roles:
            vis["query"] = {"queryState": {r: {"projections": p} for r, p in self.roles.items()}}
            if self.sort:
                vis["query"]["sortDefinition"] = {"sort": self.sort}
        if self.objects:
            vis["objects"] = self.objects
        vco = {
            "title": [{"properties": {"show": lit(bool(self.title)), **(
                {"text": lit(self.title), "fontColor": color(NAVY), "fontSize": lit(11), "bold": lit(True)}
                if self.title else {})}}],
            "background": [{"properties": {"show": lit(True), "color": color("#FFFFFF"), "transparency": lit(0)}}],
            "border": [{"properties": {"show": lit(self.vtype not in ("textbox", "actionButton", "shape")),
                                       "color": color(LIGHT), "radius": lit(6)}}],
            "dropShadow": [{"properties": {"show": lit(False)}}],
        }
        vco.update(self.container)
        vis["visualContainerObjects"] = vco
        vis["drillFilterOtherVisuals"] = True
        out = {"$schema": f"{SCHEMA}/item/report/definition/visualContainer/2.4.0/schema.json",
               "name": self.name,
               "position": {"x": self.x, "y": self.y, "z": self.z, "width": self.w, "height": self.h, "tabOrder": tab},
               "visual": vis}
        if self.filters:
            out["filterConfig"] = {"filters": self.filters}
        return out


def textbox(name: str, text: str, x: int, y: int, w: int, h: int, size: int = 18, bold: bool = True,
            fg: str = NAVY, italic: bool = False) -> Visual:
    runs = [{"value": text, "textStyle": {"fontWeight": "bold" if bold else "normal", "fontSize": f"{size}pt",
                                          "color": fg, **({"fontStyle": "italic"} if italic else {})}}]
    v = Visual(name, "textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": [{"textRuns": runs}]}}]})
    v.container = {"background": [{"properties": {"show": lit(False)}}]}
    return v


def card(name: str, measure: str, x: int, y: int, w: int = 200, h: int = 86, label: str | None = None,
         units: int = 0) -> Visual:
    """units: 0 = auto (K/M/bn, full value in tooltip), 1 = none."""
    return Visual(name, "card", x, y, w, h, roles={"Values": [proj(meas(measure))]}, title=label or measure,
                  objects={"labels": [{"properties": {"color": color(NAVY), "fontSize": lit(20),
                                                      "labelDisplayUnits": lit(units)}}],
                           "categoryLabels": [{"properties": {"show": lit(False)}}]})


def text_card(name: str, measure: str, x: int, y: int, w: int, h: int, title: str | None = None, size: int = 10) -> Visual:
    return Visual(name, "multiRowCard", x, y, w, h, roles={"Values": [proj(meas(measure))]}, title=title,
                  objects={"dataLabels": [{"properties": {"fontSize": lit(size), "color": color("#26364A")}}],
                           "categoryLabels": [{"properties": {"show": lit(False)}}],
                           "card": [{"properties": {"barShow": lit(False)}}]})


def slicer(name: str, entity: str, prop: str, x: int, y: int, w: int = 170, h: int = 56, title: str | None = None,
           single: bool = False, default: str | None = None) -> Visual:
    objects: dict[str, Any] = {
        "data": [{"properties": {"mode": lit("Dropdown")}}],
        "selection": [{"properties": {"singleSelect": lit(single), "selectAllCheckboxEnabled": lit(not single)}}],
        "header": [{"properties": {"show": lit(True), "text": lit(title or prop), "fontColor": color(NAVY)}}],
    }
    if default is not None:
        objects["general"] = [{"properties": {"filter": {"filter": {
            "Version": 2, "From": [{"Name": "d", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}},
                                                                        "Property": prop}}],
                                            "Values": [[{"Literal": {"Value": f"'{default}'"}}]]}}}]}}}}]
    return Visual(name, "slicer", x, y, w, h, roles={"Values": [proj(col(entity, prop), active=True)]},
                  objects=objects, z=2000)


def chart(name: str, vtype: str, x: int, y: int, w: int, h: int, title: str, category: dict | None = None,
          y_: list[str] | None = None, y2: list[str] | None = None, series: dict | None = None,
          tooltips: list[str] | None = None, colors: list[str] | None = None, sort: list | None = None,
          extra_objects: dict | None = None, data_labels: bool = False) -> Visual:
    roles: dict[str, list] = {}
    if category:
        roles["Category"] = [proj(category, active=True)]
    if series:
        roles["Series"] = [proj(series)]
    if y_:
        roles["Y"] = [proj(meas(m)) for m in y_]
    if y2:
        roles["Y2"] = [proj(meas(m)) for m in y2]
    if tooltips:
        roles["Tooltips"] = [proj(meas(m)) for m in tooltips]
    objects: dict[str, Any] = {
        "categoryAxis": [{"properties": {"labelColor": color(GREY), "fontSize": lit(9), "showAxisTitle": lit(False)}}],
        "valueAxis": [{"properties": {"labelColor": color(GREY), "fontSize": lit(9)}}],
        "legend": [{"properties": {"show": lit(True), "position": lit("Top"), "fontSize": lit(9)}}],
    }
    if data_labels:
        objects["labels"] = [{"properties": {"show": lit(True), "fontSize": lit(8)}}]
    if colors and y_:
        all_m = (y_ or []) + (y2 or [])
        objects["dataPoint"] = [{"properties": {"fill": color(c)}, "selector": {"metadata": f"{MEASURES}.{m}"}}
                                for m, c in zip(all_m, colors, strict=False)]
    if extra_objects:
        objects.update(extra_objects)
    return Visual(name, vtype, x, y, w, h, roles=roles, title=title, objects=objects, sort=sort)


def grade_colored(v: Visual) -> Visual:
    """Colour bars by the risk grade's colour (field value from dim_risk_grade[color_hex] via [Grade Color])."""
    v.objects["dataPoint"] = [{"properties": {"fill": {"solid": {"color": {"expr": meas("Grade Color")}}}},
                               "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}]
    return v


def table_visual(name: str, x: int, y: int, w: int, h: int, title: str, columns: list[dict], sort: list | None = None,
                 data_bars: list[str] | None = None, vtype: str = "tableEx") -> Visual:
    objects: dict[str, Any] = {
        "columnHeaders": [{"properties": {"fontColor": color("#FFFFFF"), "backColor": color(NAVY), "fontSize": lit(9)}}],
        "values": [{"properties": {"fontSize": lit(9)}}],
    }
    if data_bars:
        objects["columnFormatting"] = [{"properties": {"dataBars": {
            "positiveColor": color(BLUE), "negativeColor": color(RED), "axisColor": color(GREY),
            "reverseDirection": lit(False), "hideText": lit(False)}}, "selector": {"metadata": f"{MEASURES}.{m}"}}
            for m in data_bars]
    return Visual(name, vtype, x, y, w, h, roles={"Values": [proj(c) for c in columns]}, title=title,
                  objects=objects, sort=sort)


def matrix(name: str, x: int, y: int, w: int, h: int, title: str, rows: dict, cols: dict, values: list[str],
           gradient: bool = True, totals: bool = True) -> Visual:
    objects: dict[str, Any] = {
        "columnHeaders": [{"properties": {"fontColor": color("#FFFFFF"), "backColor": color(NAVY), "fontSize": lit(9)}}],
        "rowHeaders": [{"properties": {"fontSize": lit(9)}}],
        "values": [{"properties": {"fontSize": lit(9)}}],
    }
    if not totals:
        objects["subTotals"] = [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}]
    if gradient:
        objects["values"].append({"properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {
            "Input": meas(values[0]),
            "FillRule": {"linearGradient2": {"min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                                             "max": {"color": {"Literal": {"Value": f"'{AMBER}'"}}},
                                             "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{MEASURES}.{values[0]}"}})
    return Visual(name, "pivotTable", x, y, w, h,
                  roles={"Rows": [proj(rows, active=True)], "Columns": [proj(cols, active=True)],
                         "Values": [proj(meas(m)) for m in values]}, title=title, objects=objects)


def reset_button(name: str, group: str, x: int, y: int) -> Visual:
    """Bookmark navigator showing the page's 'Reset filters' bookmark (restores slicers to 'All')."""
    v = Visual(name, "bookmarkNavigator", x, y, 120, 30, z=3000)
    v.objects = {"bookmarks": [{"properties": {"bookmarkGroup": lit(group)}}]}
    v.container = {"background": [{"properties": {"show": lit(False)}}],
                   "border": [{"properties": {"show": lit(False)}}]}
    return v


PAGES = [("p1_executive", "1 Executive Overview"), ("p2_segmentation", "2 Risk Segmentation"),
         ("p3_repayment", "3 Repayment & Delinquency"), ("p4_model", "4 Model Performance"),
         ("p5_scenario", "5 Scenario & Expected Loss")]


def page_chrome(page_id: str, title: str, subtitle: str) -> list[Visual]:
    vs = [textbox(f"{page_id}_title", title, 16, 4, 640, 34, size=18),
          textbox(f"{page_id}_subtitle", subtitle, 16, 36, 1000, 26, size=8, bold=False, fg=GREY, italic=True),
          text_card(f"{page_id}_meta", "Header Info", 660, 4, 604, 34, size=8)]
    vs[-1].container = {"background": [{"properties": {"show": lit(False)}}],
                        "border": [{"properties": {"show": lit(False)}}]}
    # built-in page navigator: one button per page, active page highlighted automatically
    nav = Visual(f"{page_id}_nav", "pageNavigator", 16, 64, 1120, 30, z=3000)
    nav.container = {"background": [{"properties": {"show": lit(False)}}],
                     "border": [{"properties": {"show": lit(False)}}]}
    vs.append(nav)
    vs.append(reset_button(f"{page_id}_reset", f"grp_reset_{page_id}", 1144, 64))
    return vs


FCR, SEG, GR = "fact_customer_risk", "dim_customer_segment", "dim_risk_grade"
DESC = 2  # sort direction descending


def sort_by(m: str, entity: str = MEASURES, kind: str = "Measure", direction: int = DESC) -> list:
    f = meas(m, entity) if kind == "Measure" else col(entity, m)
    return [{"field": f, "direction": "Descending" if direction == DESC else "Ascending"}]


def page_executive() -> list[Visual]:
    p = "p1_executive"
    vs = page_chrome(p, "Executive Portfolio Overview",
                     "Where are customers, exposure and expected loss (scenario) concentrated?  "
                     "PD = proxy PD of Home Credit TARGET · Exposure = AMT_CREDIT proxy · LGD = 45% assumption")
    kpis = [("Total Customers", None), ("Total Exposure", "Total Exposure (proxy)"),
            ("Observed Difficulty Rate", None), ("Exposure Weighted PD", None), ("High Risk Exposure %", None),
            ("Base Expected Loss", "Expected Loss (Scenario)"), ("EL Rate", None)]
    for i, (m, lab) in enumerate(kpis):
        vs.append(card(f"{p}_kpi{i}", m, 16 + i * 178, 100, 170, 78, lab, units=1 if m == "Total Customers" else 0))
    y0 = 186
    vs += [slicer(f"{p}_s_grade", GR, "risk_grade", 16, y0, 150, title="Risk Grade"),
           slicer(f"{p}_s_income", SEG, "income_band", 172, y0, 150, title="Income band"),
           slicer(f"{p}_s_age", SEG, "age_band", 328, y0, 150, title="Age band"),
           slicer(f"{p}_s_gender", SEG, "gender", 484, y0, 150, title="Gender (diagnostic only)"),
           slicer(f"{p}_s_contract", SEG, "contract_type", 640, y0, 150, title="Contract type"),
           slicer(f"{p}_s_housing", SEG, "housing_type", 796, y0, 150, title="Housing type")]
    vs.append(slicer(f"{p}_s_occ", SEG, "occupation", 952, y0, 150, title="Occupation"))
    vs.append(slicer(f"{p}_s_family", SEG, "family_status", 1108, y0, 156, title="Family (diagnostic)"))
    vs.append(text_card(f"{p}_narr", "Executive Narrative", 16, 676, 1248, 38, title=None, size=9))
    y1 = 248
    vs.append(chart(f"{p}_grade_combo", "lineClusteredColumnComboChart", 16, y1, 410, 210,
                    "Exposure is concentrated beyond customer counts — by risk grade",
                    category=col(GR, "risk_grade"), y_=["Total Exposure"], y2=["Total Customers"],
                    colors=[BLUE, AMBER], tooltips=["Exposure Weighted PD", "Observed Difficulty Rate"]))
    vs.append(grade_colored(chart(f"{p}_grade_el", "clusteredColumnChart", 434, y1, 410, 210,
                                  "Which grades carry the expected loss (scenario)?",
                                  category=col(GR, "risk_grade"), y_=["Base Expected Loss"], data_labels=True,
                                  tooltips=["EL Rate", "Total Customers"])))
    vs.append(chart(f"{p}_share", "clusteredBarChart", 852, y1, 412, 210,
                    "Customer share vs exposure share by grade",
                    category=col(GR, "risk_grade"), y_=["Customer Share %", "Exposure Share %"],
                    colors=[LIGHT, NAVY], data_labels=True))
    y2 = 464
    vs.append(chart(f"{p}_income", "lineClusteredColumnComboChart", 16, y2, 624, 206,
                    "Exposure and exposure-weighted PD by income band",
                    category=col(SEG, "income_band"), y_=["Total Exposure"], y2=["Exposure Weighted PD"],
                    colors=[BLUE, RED], tooltips=["Total Customers", "Observed Difficulty Rate"]))
    vs.append(chart(f"{p}_age", "lineClusteredColumnComboChart", 648, y2, 616, 206,
                    "Exposure and exposure-weighted PD by age band",
                    category=col(SEG, "age_band"), y_=["Total Exposure"], y2=["Exposure Weighted PD"],
                    colors=[BLUE, RED], tooltips=["Total Customers", "Observed Difficulty Rate"]))
    return vs


def page_segmentation() -> list[Visual]:
    p = "p2_segmentation"
    vs = page_chrome(p, "Risk Segmentation",
                     "Which segments combine high PD with large exposure?  Bubble = total exposure · tooltips show "
                     "customers, observed rate, EL and portfolio share")
    vs += [slicer(f"{p}_s_grade", GR, "risk_grade", 16, 100, 150, title="Risk Grade"),
           slicer(f"{p}_s_contract", SEG, "contract_type", 16, 160, 150, title="Contract type"),
           slicer(f"{p}_s_income_type", SEG, "income_type", 16, 220, 150, title="Income type"),
           slicer(f"{p}_s_family", SEG, "family_status", 16, 280, 150, title="Family status (diagnostic)")]
    sc = Visual(f"{p}_scatter", "scatterChart", 174, 100, 540, 300,
                roles={"Category": [proj(col(SEG, "occupation"), active=True)],
                       "X": [proj(meas("Average Exposure"))], "Y": [proj(meas("Exposure Weighted PD"))],
                       "Size": [proj(meas("Total Exposure"))],
                       "Tooltips": [proj(meas(m)) for m in ("Total Customers", "Observed Difficulty Rate",
                                                             "Base Expected Loss", "Exposure Share of Portfolio %",
                                                             "EL Share of Portfolio %")]},
                title="Occupation quadrant: average exposure (x) vs exposure-weighted PD (y)",
                objects={"categoryAxis": [{"properties": {"labelColor": color(GREY), "fontSize": lit(9)}}],
                         "valueAxis": [{"properties": {"labelColor": color(GREY), "fontSize": lit(9)}}],
                         "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(7)}}],
                         "dataPoint": [{"properties": {"defaultColor": color(BLUE)}}]})
    vs.append(sc)
    vs.append(matrix(f"{p}_heat", 722, 100, 542, 300, "Exposure share: income band × risk grade (heatmap)",
                     rows=col(SEG, "income_band"), cols=col(GR, "risk_grade"),
                     values=["Exposure Share of Portfolio %"]))
    vs.append(chart(f"{p}_occ", "clusteredBarChart", 16, 408, 400, 304, "Exposure-weighted PD by occupation",
                    category=col(SEG, "occupation"), y_=["Exposure Weighted PD"], colors=[NAVY],
                    tooltips=["Total Customers", "Total Exposure", "Observed Difficulty Rate"],
                    sort=sort_by("Exposure Weighted PD")))
    top = table_visual(f"{p}_top", 424, 408, 450, 304, "Top segments by high-risk exposure (High + Critical)",
                       [col(SEG, "occupation"), col(SEG, "income_band"), meas("High Risk Customers"),
                        meas("High Risk Exposure"), meas("Exposure Weighted PD"), meas("Observed Difficulty Rate"),
                        meas("High Risk Expected Loss")],
                       sort=sort_by("High Risk Exposure"), data_bars=["High Risk Exposure"])
    vs.append(top)
    dt = Visual(f"{p}_decomp", "decompositionTreeVisual", 882, 408, 382, 304,
                roles={"Analyze": [proj(meas("High Risk Exposure"))],
                       "ExplainBy": [proj(col(GR, "risk_grade")), proj(col(SEG, "income_type")),
                                     proj(col(SEG, "education")), proj(col(SEG, "age_band")),
                                     proj(col(SEG, "housing_type"))]},
                title="Decomposition tree: high-risk exposure")
    vs.append(dt)
    return vs


def page_repayment() -> list[Visual]:
    p = "p3_repayment"
    vs = page_chrome(p, "Repayment & Delinquency",
                     "Past repayment behaviour BEFORE the current application vs current risk.  "
                     "'No history' bands are kept separate from 0 events.")
    kpis = [("Past Due Customer %", None), ("Average Max DPD", "Avg max DPD (days)"),
            ("Median Max DPD", "Median max DPD (days)"), ("Severe Delinquency Customer %", "Severe (30+ DPD) %"),
            ("Average Payment Ratio", None), ("Average Credit Utilization", "Avg card utilization")]
    for i, (m, lab) in enumerate(kpis):
        vs.append(card(f"{p}_kpi{i}", m, 16 + i * 208, 100, 200, 78, lab))
    y1 = 186
    vs.append(chart(f"{p}_dpd", "lineClusteredColumnComboChart", 16, y1, 410, 260,
                    "Max past DPD band: customers vs observed rate and PD",
                    category=col(FCR, "max_dpd_band"), y_=["Total Customers"],
                    y2=["Observed Difficulty Rate", "Average PD"], colors=[LIGHT, RED, NAVY]))
    vs.append(chart(f"{p}_payratio", "clusteredColumnChart", 434, y1, 410, 260,
                    "Underpayment goes with higher observed difficulty",
                    category=col(FCR, "payment_ratio_band"), y_=["Observed Difficulty Rate", "Average PD"],
                    colors=[RED, NAVY], data_labels=True))
    vs.append(chart(f"{p}_prev", "clusteredBarChart", 852, y1, 412, 260,
                    "Previous application outcome vs current risk",
                    category=col(FCR, "prev_outcome_group"), y_=["Observed Difficulty Rate", "Average PD"],
                    colors=[RED, NAVY], data_labels=True))
    y2 = 454
    vs.append(chart(f"{p}_bureau", "clusteredColumnChart", 16, y2, 410, 258,
                    "Active bureau credits vs observed rate and PD",
                    category=col(FCR, "bureau_active_band"), y_=["Observed Difficulty Rate", "Average PD"],
                    colors=[RED, NAVY]))
    vs.append(chart(f"{p}_util", "lineClusteredColumnComboChart", 434, y2, 410, 258,
                    "Card utilization: exposure and PD",
                    category=col(FCR, "utilization_band"), y_=["Total Exposure"], y2=["Average PD"],
                    colors=[BLUE, RED], tooltips=["Total Customers", "Observed Difficulty Rate"]))
    vs.append(table_visual(f"{p}_detail", 852, y2, 412, 258, "Delinquency detail by max DPD band",
                           [col(FCR, "max_dpd_band"), meas("Total Customers"), meas("Total Exposure"),
                            meas("Observed Difficulty Rate"), meas("Average PD"), meas("Average Payment Ratio")],
                           data_bars=["Observed Difficulty Rate"]))
    return vs


def page_model() -> list[Visual]:
    p = "p4_model"
    vs = page_chrome(p, "Model Performance & Monitoring",
                     "Default view = HOLDOUT (unseen, evaluated once). Train scores are in-sample and not used to "
                     "judge performance. Bins/groups with small samples are flagged.")
    vs += [slicer(f"{p}_s_split", "dim_split", "split_label", 16, 100, 190, title="Dataset split", single=True,
                  default="Holdout (evaluation)"),
           slicer(f"{p}_s_ver", "dim_model_version", "model_version", 16, 160, 190, title="Model version", single=True)]
    kpis = ["ROC-AUC", "PR-AUC", "Brier Score", "KS", "Selected Threshold", "Recall at Threshold",
            "Precision at Threshold"]
    for i, m in enumerate(kpis):
        vs.append(card(f"{p}_kpi{i}", m, 214 + i * 150, 100, 144, 72, m))
    vs.append(text_card(f"{p}_narr", "Model Summary Narrative", 214, 178, 1050, 40, size=8))
    y1 = 224
    vs.append(chart(f"{p}_roc", "lineChart", 16, y1, 270, 240, "ROC curve (calibrated PD)",
                    category=col("fact_model_curve", "x"), y_=["ROC True Positive Rate", "Random Classifier"],
                    colors=[NAVY, GREY]))
    vs.append(chart(f"{p}_pr", "lineChart", 294, y1, 270, 240, "Precision–Recall curve",
                    category=col("fact_model_curve", "x"), y_=["PR Precision"], colors=[BLUE]))
    vs.append(chart(f"{p}_cal", "lineChart", 572, y1, 300, 240, "Calibration: mean predicted PD vs observed rate (deciles)",
                    category=col("fact_calibration", "pd_bin"), y_=["Calibration Mean PD", "Calibration Observed Rate"],
                    colors=[NAVY, AMBER], tooltips=["Calibration Bin Customers", "Raw Score Mean PD"]))
    vs.append(matrix(f"{p}_cm", 880, y1, 384, 240, "Confusion matrix @ frozen threshold",
                     rows=col("fact_confusion_matrix", "actual"), cols=col("fact_confusion_matrix", "predicted"),
                     values=["Confusion Count"], totals=False))
    y2 = 472
    vs.append(chart(f"{p}_lift", "lineClusteredColumnComboChart", 16, y2, 400, 240, "Decile lift & cumulative capture",
                    category=col("fact_lift", "decile"), y_=["Decile Lift"],
                    y2=["Cumulative Capture", "Cumulative Exposure Capture"], colors=[NAVY, AMBER, RED]))
    vs.append(chart(f"{p}_imp", "clusteredBarChart", 424, y2, 400, 240, "Global importance — mean |SHAP| top 15",
                    category=col("fact_feature_importance", "feature"), y_=["Top 15 Mean abs SHAP"], colors=[NAVY],
                    sort=sort_by("Top 15 Mean abs SHAP")))
    vs.append(table_visual(f"{p}_groups", 832, y2, 432, 240, "Group diagnostics (descriptive, not proof of bias)",
                           [col("fact_segment_performance", c) for c in
                            ("group_attribute", "group_value", "customers", "observed_rate", "mean_pd", "roc_auc",
                             "recall_at_threshold", "small_sample_warning")]))
    return vs


def page_scenario() -> list[Visual]:
    p = "p5_scenario"
    vs = page_chrome(p, "Scenario & Expected Loss",
                     "What happens to expected loss if PD, LGD or exposure deteriorate?  Stress values are relative "
                     "multipliers (+20% = ×1.2); stressed PD is capped at 100%.")
    vs += [slicer(f"{p}_s_pd", "PD Stress", "PD Stress Value", 16, 100, 200, title="PD Stress % (relative)", single=True),
           slicer(f"{p}_s_lgd", "LGD Assumption", "LGD Assumption Value", 16, 160, 200,
                  title="LGD Assumption % (default 45%)", single=True),
           slicer(f"{p}_s_exp", "Exposure Change", "Exposure Change Value", 16, 220, 200, title="Exposure Change %",
                  single=True)]
    kpis = [("Base Expected Loss", "Base EL"), ("Scenario Expected Loss", "Scenario EL"),
            ("Scenario EL Change", "EL change"), ("Scenario EL Change %", "EL change %"),
            ("Scenario EL Rate", "Scenario EL rate")]
    for i, (m, lab) in enumerate(kpis):
        vs.append(card(f"{p}_kpi{i}", m, 224 + i * 208, 100, 200, 78, lab))
    vs.append(chart(f"{p}_wf", "waterfallChart", 224, 186, 520, 250, "Base → Scenario EL bridge",
                    category=col("EL Bridge", "Step"), y_=["EL Bridge Value"],
                    extra_objects={"sentimentColors": [{"properties": {"increaseFill": color(RED),
                                                                       "decreaseFill": color(BLUE),
                                                                       "totalFill": color(NAVY)}}]}))
    vs.append(grade_colored(chart(f"{p}_grade", "clusteredColumnChart", 752, 186, 512, 250,
                                  "Scenario EL by risk grade", category=col(GR, "risk_grade"),
                                  y_=["Scenario Expected Loss"], tooltips=["Base Expected Loss", "Scenario EL Change"],
                                  data_labels=True)))
    vs.append(chart(f"{p}_stack", "barChart", 16, 444, 400, 268, "Loss contribution by income band and grade",
                    category=col(SEG, "income_band"), series=col(GR, "risk_grade"), y_=["Scenario Expected Loss"]))
    vs.append(matrix(f"{p}_sens", 424, 444, 440, 268, "Sensitivity: PD stress × LGD → expected loss",
                     rows=col("Sensitivity PD", "PD Stress Level"), cols=col("Sensitivity LGD", "LGD Level"),
                     values=["Sensitivity EL"]))
    vs.append(table_visual(f"{p}_top", 872, 444, 392, 210, "Top loss-contributing segments",
                           [col(SEG, "occupation"), meas("Scenario Expected Loss"), meas("Scenario EL Share %"),
                            meas("Exposure Weighted PD")], sort=sort_by("Scenario Expected Loss"),
                           data_bars=["Scenario Expected Loss"]))
    vs.append(textbox(f"{p}_disclaimer",
                      "본 페이지는 AMT_CREDIT을 Exposure proxy로, 선택한 LGD를 가정값으로 사용하는 포트폴리오 민감도 분석이며 "
                      "실제 충당금·규제자본 산출이 아닙니다.", 872, 658, 392, 54, size=8, bold=True, fg=RED))
    return vs


def _find_base_theme(name: str) -> Path | None:
    """Base themes ship with Power BI Desktop; a Desktop-saved PBIR keeps a copy under StaticResources."""
    existing = PBI_DIR / f"{NAME}.Report" / "StaticResources" / "SharedResources" / "BaseThemes" / f"{name}.json"
    rel = f"bin/WebView2Resources/minerva/sharedresources/BaseThemes/{name}.json"
    candidates = [Path("C:/Program Files/Microsoft Power BI Desktop") / rel]
    try:  # Store edition: WindowsApps cannot be listed, ask Appx for the install location
        import subprocess

        loc = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop).InstallLocation"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        if loc:
            candidates.insert(0, Path(loc) / rel)
    except (OSError, subprocess.SubprocessError):
        pass
    for c in candidates:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return existing if existing.exists() else None


def write_report(root: Path, data_source_note: str, active_page: str | None = None) -> None:
    rp = root / f"{NAME}.Report"
    d = rp / "definition"
    if d.exists():
        shutil.rmtree(d)
    _write(rp / ".platform", json.dumps({
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": "8b2f3c6d-1e4a-4f7b-8c9d-0a1b2c3d4e5f"}}, indent=2))
    _write(rp / "definition.pbir", json.dumps({
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json", "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2))
    _write(rp / ".pbi" / "localSettings.json", json.dumps({
        "$schema": f"{SCHEMA}/item/report/localSettings/1.0.0/schema.json"}, indent=2))
    base_theme = _find_base_theme("CY25SU10")
    if base_theme:
        dst = rp / "StaticResources" / "SharedResources" / "BaseThemes" / "CY25SU10.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base_theme, dst)
    theme_src = PBI_DIR / "theme.json"
    theme_dst = rp / "StaticResources" / "RegisteredResources" / "CreditRiskTheme.json"
    theme_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(theme_src, theme_dst)
    _write(d / "version.json", json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2))
    _write(d / "report.json", json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/report/3.0.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": "CY25SU10", "reportVersionAtImport": {"visual": "2.1.0", "report": "3.0.0",
                                                                         "page": "2.3.0"}, "type": "SharedResources"},
            "customTheme": {"name": "CreditRiskTheme.json", "reportVersionAtImport": {"visual": "2.1.0",
                                                                                      "report": "3.0.0", "page": "2.3.0"},
                            "type": "RegisteredResources"}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "CreditRiskTheme.json", "path": "CreditRiskTheme.json",
                                         "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True, "useEnhancedTooltips": True},
        "annotations": [{"name": "data_source_note", "value": data_source_note}],
    }, indent=2))
    builders = {"p1_executive": page_executive, "p2_segmentation": page_segmentation, "p3_repayment": page_repayment,
                "p4_model": page_model, "p5_scenario": page_scenario}
    _write(d / "pages" / "pages.json", json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [pid for pid, _ in PAGES], "activePageName": active_page or PAGES[0][0]}, indent=2))
    bookmarks = []
    for pid, display in PAGES:
        pd_ = d / "pages" / pid
        _write(pd_ / "page.json", json.dumps({
            "$schema": f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json", "name": pid,
            "displayName": display, "displayOption": "FitToPage", "height": 720, "width": 1280,
            "objects": {"background": [{"properties": {"color": color(BG), "transparency": lit(0)}}],
                        "outspace": [{"properties": {"color": color(BG)}}]}}, indent=2))
        visuals = builders[pid]()
        for i, v in enumerate(visuals):
            _write(pd_ / "visuals" / v.name / "visual.json", json.dumps(v.to_json(i * 100), indent=2, ensure_ascii=False))
        bookmarks.append({"name": f"bm_reset_{pid}", "displayName": "Reset filters", "page": pid,
                          "slicers": [v.name for v in visuals if v.vtype == "slicer"]})
    # Reset bookmarks: data-only state of the page with every slicer cleared
    for b in bookmarks:
        _write(d / "bookmarks" / f"{b['name']}.bookmark.json", json.dumps({
            "$schema": f"{SCHEMA}/item/report/definition/bookmark/1.4.0/schema.json",
            "displayName": b["displayName"], "name": b["name"],
            "options": {"suppressActiveSection": True, "suppressDisplay": True},
            "explorationState": {"version": "1.3", "activeSection": b["page"],
                                 "sections": {b["page"]: {"visualContainers": {
                                     s_: {"filters": {"byExpr": []}} for s_ in b["slicers"]}}}}}, indent=2))
    _write(d / "bookmarks" / "bookmarks.json", json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/bookmarksMetadata/1.0.0/schema.json",
        "items": [{"name": f"grp_reset_{b['page']}", "displayName": f"Reset {b['page']}", "children": [b["name"]]}
                  for b in bookmarks]}, indent=2))


def build_pbip(settings: Settings, data_source: str = "PostgreSQL", active_page: str | None = None) -> Path:
    root = PBI_DIR
    tables = build_semantic_model(settings)
    sm_def = root / f"{NAME}.SemanticModel" / "definition"
    if sm_def.exists():
        shutil.rmtree(sm_def)
    write_semantic_model(settings, root, tables, data_source)
    write_report(root, f"DataSource parameter default = {data_source}", active_page)
    _write(root / f"{NAME}.pbip", json.dumps({
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}}, indent=2))
    _write_model_docs(tables)
    log.info("PBIP written to %s (%d tables, %d measures)", root, len(tables),
             sum(len(t.measures) for t in tables))
    return root / f"{NAME}.pbip"


def _write_model_docs(tables: list[Table]) -> None:
    """powerbi/dax_measures.md is generated from the same measure list the model uses."""
    lines = ["# DAX Measures (`00_Measures`)", "",
             "_자동 생성: `credit-risk build-pbip`. 모델과 동일한 정의에서 생성되므로 문서와 PBIP가 항상 일치한다._", "",
             "업무 규칙(Exposure proxy, LGD, EL, Risk Grade, 연체 구간)은 SQL mart에서 정의되고, DAX는 집계와 What-if "
             "배율만 담당한다. What-if 값은 **상대 배율**이다(+20% = ×1.2, percentage point 아님).", ""]
    meas_table = next(t for t in tables if t.name == MEASURES)
    folder = None
    for m in sorted(meas_table.measures, key=lambda x: (x.folder or "", 0)):
        if m.folder != folder:
            folder = m.folder
            lines += [f"## {folder}", ""]
        lines += [f"### {m.name}", f"- Format: `{m.fmt or 'text'}`" + (f" · {m.description}" if m.description else ""),
                  "```DAX", f"{m.name} =", m.expr, "```", ""]
    _write(PBI_DIR / "dax_measures.md", "\n".join(lines))

    dm = ["# Power BI Data Model", "",
          "_자동 생성: `credit-risk build-pbip` — 아래 내용은 PBIP 시맨틱 모델과 동일한 정의에서 만들어진다._", "",
          "## Star schema", "", "```text",
          "dim_risk_grade ──1:N──► fact_customer_risk ◄──N:1── dim_customer_segment",
          "                         ▲            ▲",
          "dim_model_version ──1:N──┘            └──N:1── dim_split",
          "dim_risk_grade / dim_customer_segment ──1:N──► fact_scenario_base",
          "dim_model_version / dim_split ──1:N──► fact_model_evaluation · fact_model_curve · fact_calibration · fact_lift",
          "                                      fact_segment_performance · fact_confusion_matrix (+ fact_feature_importance)",
          "Disconnected: 00_Measures · PD Stress · LGD Assumption · Exposure Change · Sensitivity PD · Sensitivity LGD · "
          "EL Bridge · report_metadata", "```", "",
          "- 모든 관계: **dimension → fact 단방향, 1:N**, 양방향·다대다 없음. 숫자 key·sort 컬럼은 숨김, 요약 안 함.",
          "- Import mode. 데이터 소스는 `DataSource` 파라미터로 선택: `PostgreSQL`(기본, `mart` 스키마) 또는 "
          "`CSV`(`credit-risk export-bi` 스냅샷). 자격 증명은 PBIX/PBIP에 저장되지 않는다.",
          "- Power Query는 원천 선택 + 타입 지정 2단계만 수행(`Source`, `Typed columns`). 행 수준 규칙은 모두 SQL mart에 있다.", "",
          "## Relationships", "", "| From (many) | To (one) | Direction |", "|---|---|---|"]
    dm += [f"| `{ft}[{fc}]` | `{dt}[{dc}]` | single (dim → fact) |" for dt, dc, ft, fc in RELATIONSHIPS]
    dm += ["", "## Tables", "", "| Table | Kind | Columns | Measures | Source |", "|---|---|---:|---:|---|"]
    for t in tables:
        kind = "measure table" if t.name == MEASURES else "calculated (What-if)" if t.calculated else \
            "fact" if t.name.startswith("fact_") else "dimension" if t.name.startswith("dim_") else "metadata"
        src = f"`mart.{t.source_table}`" if t.source_table else (f"`{t.calculated[:60]}`" if t.calculated else "—")
        dm.append(f"| `{t.name}` | {kind} | {len(t.columns)} | {len(t.measures)} | {src} |")
    dm += ["", "## What-if parameters (relative multipliers)", "",
           "| Table[column] | Range | Default (no selection) | Meaning |", "|---|---|---|---|",
           "| `'PD Stress'[PD Stress Value]` | −20% … +100%, step 5% | 0% | PD × (1 + value), capped at 1 |",
           "| `'LGD Assumption'[LGD Assumption Value]` | 10% … 90%, step 5% | 45% | replaces the base LGD assumption |",
           "| `'Exposure Change'[Exposure Change Value]` | −30% … +30%, step 5% | 0% | exposure × (1 + value) |",
           "", "+20%는 **×1.2 (상대 배율)** 이며 +20 percentage point가 아니다. 시나리오 measure는 `dax_measures.md` 참조."]
    _write(PBI_DIR / "data_model.md", "\n".join(dm) + "\n")
