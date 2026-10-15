"""Data quality checks (spec §9.2, §17.2).

Every check produces a row in audit.data_quality_result. Severity policy (§9.3):
* fail  -> core key duplicates/nulls, TARGET domain errors, customer-grain violations, mart mismatches
* warn  -> orphan ratios / null ratios / unexpected categories above configured thresholds
After persisting, `raise_on_fail()` aborts the pipeline if any check failed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from .config import Settings

log = logging.getLogger(__name__)


class DataQualityError(RuntimeError):
    pass


@dataclass
class CheckResult:
    layer: str
    check_name: str
    table_name: str | None
    column_name: str | None
    status: str
    observed_value: float | None
    expected_rule: str
    details: dict[str, Any] = field(default_factory=dict)


class QualityRunner:
    def __init__(self, conn: psycopg.Connection, run_id: int, layer: str):
        self.conn = conn
        self.run_id = run_id
        self.layer = layer
        self.results: list[CheckResult] = []

    # ---------------------------------------------------------------- primitives
    def add(self, check: str, table: str | None, column: str | None, status: str, observed: Any,
            rule: str, **details: Any) -> CheckResult:
        obs = None if observed is None else float(observed)
        res = CheckResult(self.layer, check, table, column, status, obs, rule, details)
        self.results.append(res)
        if status != "pass":
            log.warning("[%s] %-28s %-40s %-28s observed=%s rule=%s", status.upper(), check, table, column or "",
                        observed, rule)
        return res

    def scalar(self, query: sql.Composable | str, params: Any = None) -> Any:
        row = self.conn.execute(query, params).fetchone()
        return None if row is None else row[0]

    def table_exists(self, table: str) -> bool:
        return self.scalar("SELECT to_regclass(%s) IS NOT NULL", (table,))

    @staticmethod
    def ident(table: str) -> sql.Identifier:
        return sql.Identifier(*table.split("."))

    # ---------------------------------------------------------------- generic checks
    def row_count_positive(self, table: str) -> int:
        n = self.scalar(sql.SQL("SELECT count(*) FROM {}").format(self.ident(table)))
        self.add("row_count_positive", table, None, "pass" if n > 0 else "fail", n, "count(*) > 0")
        return n

    def key_not_null(self, table: str, keys: list[str], severity: str = "fail") -> None:
        cond = sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(sql.Identifier(k)) for k in keys)
        n = self.scalar(sql.SQL("SELECT count(*) FROM {} WHERE {}").format(self.ident(table), cond))
        self.add("key_not_null", table, ",".join(keys), "pass" if n == 0 else severity, n, "null key rows = 0")

    def key_unique(self, table: str, keys: list[str], severity: str = "fail") -> None:
        cols = sql.SQL(", ").join(map(sql.Identifier, keys))
        q = sql.SQL("SELECT count(*) - count(DISTINCT ({cols})) FROM {t}").format(cols=cols, t=self.ident(table)) \
            if len(keys) > 1 else \
            sql.SQL("SELECT count(*) - count(DISTINCT {cols}) FROM {t}").format(cols=cols, t=self.ident(table))
        dup = self.scalar(q)
        self.add("key_unique", table, ",".join(keys), "pass" if dup == 0 else severity, dup, "duplicate key rows = 0")

    def accepted_values(self, table: str, column: str, values: list[Any], allow_null: bool = False,
                        severity: str = "fail", where: str | None = None) -> None:
        cond = sql.SQL("NOT ({c} = ANY(%s))").format(c=sql.Identifier(column))
        if not allow_null:
            cond = sql.SQL("({} OR {} IS NULL)").format(cond, sql.Identifier(column))
        q = sql.SQL("SELECT count(*) FROM {t} WHERE {cond}").format(t=self.ident(table), cond=cond)
        if where:
            q = q + sql.SQL(" AND " + where)
        bad = self.scalar(q, (values,))
        self.add("accepted_values", table, column, "pass" if bad == 0 else severity, bad,
                 f"{column} in {values}{' or null' if allow_null else ''}")

    def expression_count(self, check: str, table: str, column: str | None, condition: str, rule: str,
                         severity: str = "fail", max_allowed: int = 0) -> int:
        """Count rows matching a *violation* condition (SQL text from code, never user input)."""
        n = self.scalar(sql.SQL("SELECT count(*) FROM {} WHERE " + condition).format(self.ident(table)))
        self.add(check, table, column, "pass" if n <= max_allowed else severity, n, rule)
        return n

    def orphan_ratio(self, child: str, child_key: str, parent: str, parent_key: str,
                     warn_ratio: float, fail_ratio: float) -> float:
        q = sql.SQL("""
            SELECT count(*) FILTER (WHERE p.k IS NULL)::float / NULLIF(count(*), 0)
              FROM (SELECT DISTINCT {ck} AS k FROM {c}) ch
              LEFT JOIN (SELECT DISTINCT {pk} AS k FROM {p}) p ON p.k = ch.k
        """).format(ck=sql.Identifier(child_key), c=self.ident(child), pk=sql.Identifier(parent_key),
                    p=self.ident(parent))
        ratio = float(self.scalar(q) or 0.0)
        status = "fail" if ratio > fail_ratio else "warn" if ratio > warn_ratio else "pass"
        self.add("orphan_ratio", child, child_key, status, round(ratio, 6),
                 f"distinct {child_key} not in {parent}.{parent_key}: warn>{warn_ratio}, fail>{fail_ratio}",
                 parent=parent)
        return ratio

    def null_ratios(self, table: str, warn_above: float) -> dict[str, float]:
        cols = [r[0] for r in self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
            "ORDER BY ordinal_position", table.split(".")).fetchall()]
        exprs = sql.SQL(", ").join(sql.SQL("count({})").format(sql.Identifier(c)) for c in cols)
        row = self.conn.execute(sql.SQL("SELECT count(*), {} FROM {}").format(exprs, self.ident(table))).fetchone()
        total = row[0] or 1
        ratios = {c: 1 - (v / total) for c, v in zip(cols, row[1:], strict=True)}
        high = {c: round(r, 4) for c, r in ratios.items() if r > warn_above}
        self.add("null_ratio_summary", table, None, "warn" if high else "pass", len(high),
                 f"columns with null ratio > {warn_above}", columns_above=high,
                 all_ratios={c: round(r, 4) for c, r in ratios.items()})
        return ratios

    def equal_counts(self, check: str, query_a: str, query_b: str, table: str, rule: str,
                     severity: str = "fail") -> None:
        a, b = self.scalar(query_a), self.scalar(query_b)
        self.add(check, table, None, "pass" if a == b else severity, (a or 0) - (b or 0), rule, left=a, right=b)

    # ---------------------------------------------------------------- persistence
    def persist(self) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO audit.data_quality_result
                       (run_id, layer, check_name, table_name, column_name, status, observed_value, expected_rule, details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(self.run_id, r.layer, r.check_name, r.table_name, r.column_name, r.status, r.observed_value,
                  r.expected_rule, json.dumps(r.details, default=str)) for r in self.results],
            )
        self.conn.commit()

    def summary(self) -> dict[str, int]:
        out = {"pass": 0, "warn": 0, "fail": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def raise_on_fail(self) -> None:
        fails = [r for r in self.results if r.status == "fail"]
        if fails:
            msg = "; ".join(f"{r.check_name}:{r.table_name}.{r.column_name}={r.observed_value}" for r in fails[:10])
            raise DataQualityError(f"{len(fails)} data quality check(s) failed: {msg}")


# =============================================================================== layer suites
CORE_UNIQUE = {"application_train", "application_test", "bureau", "previous_application"}


def raw_checks(conn: psycopg.Connection, settings: Settings, run_id: int) -> QualityRunner:
    q = QualityRunner(conn, run_id, "raw")
    sources = settings.project["sources"]
    null_warn = settings.project["quality"]["null_ratio_warn"]
    for table, cfg in sources.items():
        full = f"raw.{table}"
        q.row_count_positive(full)
        keys = cfg.get("unique_key")
        if keys:
            keys = [k.lower() for k in keys]
            q.key_not_null(full, keys)
            q.key_unique(full, keys, severity="fail" if table in CORE_UNIQUE else "warn")
        if table != "columns_description":
            q.null_ratios(full, null_warn)

    q.accepted_values("raw.application_train", "target", [0, 1])
    overlap = q.scalar("SELECT count(*) FROM raw.application_train a JOIN raw.application_test t USING (sk_id_curr)")
    q.add("train_test_id_overlap", "raw.application_train", "sk_id_curr", "pass" if overlap == 0 else "fail",
          overlap, "train ∩ test SK_ID_CURR = 0")

    conn.execute("""CREATE OR REPLACE VIEW raw.application_all AS
                    SELECT sk_id_curr FROM raw.application_train
                    UNION ALL SELECT sk_id_curr FROM raw.application_test""")
    for rel in settings.project["relationships"]:
        q.orphan_ratio(f"raw.{rel['child']}", rel["child_key"].lower(), f"raw.{rel['parent']}",
                       rel["parent_key"].lower(), rel["warn_ratio"], rel["fail_ratio"])

    # impossible / suspicious values on the application population
    sentinel = settings.project["quality"]["days_employed_sentinel"]
    app = "raw.application_train"
    q.expression_count("impossible_value", app, "amt_credit", "amt_credit IS NULL OR amt_credit <= 0",
                       "amt_credit > 0 (exposure proxy)")
    q.expression_count("impossible_value", app, "amt_income_total", "amt_income_total <= 0", "amt_income_total > 0")
    q.expression_count("impossible_value", app, "days_birth", "days_birth >= 0", "days_birth < 0 (relative days)")
    q.expression_count("impossible_value", app, "cnt_children", "cnt_children < 0", "cnt_children >= 0")
    for c in ("ext_source_1", "ext_source_2", "ext_source_3"):
        q.expression_count("impossible_value", app, c, f"{c} < 0 OR {c} > 1", f"{c} in [0,1]")
    n_sent = q.expression_count("known_sentinel", app, "days_employed", f"days_employed = {sentinel}",
                                f"rows with DAYS_EMPLOYED = {sentinel} (cleaned to NULL in staging)",
                                severity="warn", max_allowed=10**12)
    q.expression_count("impossible_value", app, "days_employed",
                       f"days_employed > 0 AND days_employed <> {sentinel}",
                       "positive DAYS_EMPLOYED other than the sentinel", severity="warn")
    log.info("days_employed sentinel rows: %s", n_sent)
    for tbl, col in [("bureau", "days_credit"), ("previous_application", "days_decision"),
                     ("installments_payments", "days_instalment"), ("pos_cash_balance", "months_balance"),
                     ("credit_card_balance", "months_balance"), ("bureau_balance", "months_balance")]:
        q.expression_count("post_application_record", f"raw.{tbl}", col, f"{col} > 0",
                           f"{col} <= 0 (history must precede the current application)", severity="warn")

    _numeric_profile(q, app)
    _categorical_drift(q, "raw.application_train", "raw.application_test")
    q.persist()
    return q


def _numeric_profile(q: QualityRunner, table: str) -> None:
    cols = [r[0] for r in q.conn.execute(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s AND data_type='double precision' ORDER BY ordinal_position""",
        table.split(".")).fetchall()]
    if not cols:
        return
    parts = sql.SQL(", ").join(
        sql.SQL("min({c}), max({c}), percentile_cont(ARRAY[0.01,0.5,0.99]) WITHIN GROUP (ORDER BY {c})")
        .format(c=sql.Identifier(c)) for c in cols)
    row = q.conn.execute(sql.SQL("SELECT {} FROM {}").format(parts, q.ident(table))).fetchone()
    profile = {}
    for i, c in enumerate(cols):
        mn, mx, pct = row[3 * i: 3 * i + 3]
        profile[c] = {"min": mn, "max": mx, "p01": pct[0] if pct else None, "p50": pct[1] if pct else None,
                      "p99": pct[2] if pct else None}
    q.add("numeric_profile", table, None, "pass", len(cols), "min/max/p01/p50/p99 recorded", profile=profile)


def _categorical_drift(q: QualityRunner, train: str, test: str) -> None:
    cols = [r[0] for r in q.conn.execute(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s AND data_type='text' ORDER BY ordinal_position""",
        train.split(".")).fetchall()]
    for c in cols:
        card = q.scalar(sql.SQL("SELECT count(DISTINCT {c}) FROM {t}").format(c=sql.Identifier(c), t=q.ident(train)))
        new_vals = [r[0] for r in q.conn.execute(sql.SQL(
            "SELECT DISTINCT {c} FROM {te} WHERE {c} IS NOT NULL EXCEPT SELECT DISTINCT {c} FROM {tr}"
        ).format(c=sql.Identifier(c), te=q.ident(test), tr=q.ident(train))).fetchall()]
        q.add("categorical_cardinality", train, c, "warn" if new_vals else "pass", card,
              "values in application_test must exist in application_train", new_in_test=new_vals[:20])
