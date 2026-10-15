"""Raw layer ingestion (spec §9.1).

* Files are located case-insensitively (Windows/Linux safe).
* Column types are inferred from a full chunked scan of each CSV (cached by file fingerprint),
  so the raw layer keeps every original column with the narrowest safe type:
  identifiers -> bigint, TARGET -> smallint, numeric -> double precision, everything else -> text.
* Column names are lower-cased so SQL never needs quoted identifiers.
* Data is streamed into PostgreSQL with client-side COPY; each table is rebuilt atomically.
* Every load is written to audit.table_load_log; unchanged files are skipped on re-run.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

from .config import Settings
from .utils import file_fingerprint, write_json

log = logging.getLogger(__name__)

ID_COLUMNS = {"SK_ID_CURR", "SK_ID_BUREAU", "SK_ID_PREV"}
SCHEMA_CACHE = "raw_schema_cache.json"


class IngestError(RuntimeError):
    pass


@dataclass
class ColumnSpec:
    source_name: str
    name: str
    pg_type: str


@dataclass
class TableSpec:
    table: str
    path: Path
    encoding: str
    columns: list[ColumnSpec]
    csv_rows: int
    fingerprint: dict


def find_file(data_dir: Path, filename: str) -> Path | None:
    """Case-insensitive lookup of `filename` inside data_dir."""
    if not data_dir.exists():
        return None
    lower = filename.lower()
    # scan instead of Path.exists(): on case-insensitive file systems that would return the *requested*
    # spelling rather than the real file name
    for candidate in sorted(data_dir.iterdir()):
        if candidate.is_file() and candidate.name.lower() == lower:
            return candidate
    return None


def normalise_name(name: str, position: int) -> str:
    name = name.strip()
    if not name or name.lower().startswith("unnamed"):
        return "row_idx" if position == 0 else f"col_{position}"
    return name.lower()


def read_header(path: Path, encoding: str) -> list[str]:
    with path.open(encoding=encoding, newline="") as fh:
        return next(csv.reader(fh))


def _infer_types(path: Path, header: list[str], encoding: str) -> tuple[dict[str, str], int]:
    """Full chunked scan: a column is numeric only if pandas parsed it as numeric in every chunk."""
    kinds: dict[str, set[str]] = {c: set() for c in header}
    rows = 0
    reader = pd.read_csv(path, chunksize=250_000, encoding=encoding, low_memory=False,
                         keep_default_na=False, na_values=[""])
    for chunk in reader:
        rows += len(chunk)
        for col, dtype in zip(header, chunk.dtypes, strict=True):
            if pd.api.types.is_integer_dtype(dtype) or pd.api.types.is_bool_dtype(dtype):
                kinds[col].add("int")
            elif pd.api.types.is_float_dtype(dtype):
                kinds[col].add("float" if chunk[col].notna().any() else "empty")
            else:
                kinds[col].add("text")
    types: dict[str, str] = {}
    for col, seen in kinds.items():
        if col.upper() in ID_COLUMNS:
            types[col] = "bigint"
        elif col.upper() == "TARGET":
            types[col] = "smallint"
        elif "text" in seen:
            types[col] = "text"
        else:
            types[col] = "double precision"
    return types, rows


def build_table_spec(settings: Settings, table: str, source_cfg: dict, cache: dict) -> TableSpec:
    path = find_file(settings.data_dir, source_cfg["file"])
    if path is None:
        raise IngestError(f"required file not found: {source_cfg['file']} in {settings.data_dir}")
    encoding = source_cfg.get("encoding", "utf-8")
    header = read_header(path, encoding)
    missing = [c for c in source_cfg.get("required_columns", []) if c not in header]
    if missing:
        raise IngestError(f"{path.name}: missing expected columns {missing}")

    fp = file_fingerprint(path)
    cache_key = f"{table}|{fp['bytes']}|{fp['mtime']}"
    if cache_key in cache:
        types, rows = cache[cache_key]["types"], cache[cache_key]["rows"]
    else:
        t0 = time.perf_counter()
        types, rows = _infer_types(path, header, encoding)
        cache[cache_key] = {"types": types, "rows": rows}
        log.info("inferred schema %-25s %10d rows %4d cols in %.1fs", table, rows, len(header), time.perf_counter() - t0)

    columns = [ColumnSpec(c, normalise_name(c, i), types[c]) for i, c in enumerate(header)]
    return TableSpec(table, path, encoding, columns, rows, fp)


def _already_loaded(conn: psycopg.Connection, spec: TableSpec) -> bool:
    row = conn.execute(
        """SELECT db_rows, details FROM audit.table_load_log
            WHERE table_name = %s AND status = 'loaded'
            ORDER BY load_id DESC LIMIT 1""",
        (f"raw.{spec.table}",),
    ).fetchone()
    if not row:
        return False
    exists = conn.execute("SELECT to_regclass(%s)", (f"raw.{spec.table}",)).fetchone()[0]
    if exists is None:
        return False
    details = row[1] or {}
    if details.get("fingerprint") != spec.fingerprint or row[0] != spec.csv_rows:
        return False
    current = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("raw", spec.table))).fetchone()[0]
    return current == spec.csv_rows


def load_table(conn: psycopg.Connection, spec: TableSpec, run_id: int, force: bool = False) -> dict:
    target = sql.Identifier("raw", spec.table)
    if not force and _already_loaded(conn, spec):
        log.info("skip  raw.%-25s unchanged (%d rows)", spec.table, spec.csv_rows)
        conn.execute(
            """INSERT INTO audit.table_load_log
                   (run_id, table_name, source_file, source_bytes, csv_rows, db_rows, column_count, status, duration_s, details)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'skipped_unchanged',0,%s)""",
            (run_id, f"raw.{spec.table}", spec.path.name, spec.fingerprint["bytes"], spec.csv_rows,
             spec.csv_rows, len(spec.columns), json.dumps({"fingerprint": spec.fingerprint})),
        )
        conn.commit()
        return {"table": spec.table, "status": "skipped_unchanged", "rows": spec.csv_rows}

    t0 = time.perf_counter()
    staging_name = f"{spec.table}__loading"
    tmp = sql.Identifier("raw", staging_name)
    col_defs = sql.SQL(", ").join(
        sql.SQL("{} {}").format(sql.Identifier(c.name), sql.SQL(c.pg_type)) for c in spec.columns
    )
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(tmp))
    conn.execute(sql.SQL("CREATE UNLOGGED TABLE {} ({})").format(tmp, col_defs))
    copy_sql = sql.SQL(
        "COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '', ENCODING {})"
    ).format(tmp, sql.SQL(", ").join(sql.Identifier(c.name) for c in spec.columns),
             sql.Literal("LATIN1" if spec.encoding.lower() in {"latin-1", "latin1", "iso-8859-1"} else "UTF8"))
    with spec.path.open("rb") as fh, conn.cursor().copy(copy_sql) as copy:
        while block := fh.read(8 << 20):
            copy.write(block)
    db_rows = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(tmp)).fetchone()[0]
    if db_rows != spec.csv_rows:
        raise IngestError(f"raw.{spec.table}: row count mismatch csv={spec.csv_rows} db={db_rows}")
    # atomic swap: the old table disappears only once the new one is complete
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(target))
    conn.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(tmp, sql.Identifier(spec.table)))
    conn.execute(sql.SQL("ALTER TABLE {} SET LOGGED").format(target))
    conn.execute(sql.SQL("ANALYZE {}").format(target))
    duration = time.perf_counter() - t0
    conn.execute(
        """INSERT INTO audit.table_load_log
               (run_id, table_name, source_file, source_bytes, csv_rows, db_rows, column_count, status, duration_s, details)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'loaded',%s,%s)""",
        (run_id, f"raw.{spec.table}", spec.path.name, spec.fingerprint["bytes"], spec.csv_rows, db_rows,
         len(spec.columns), round(duration, 2),
         json.dumps({"fingerprint": spec.fingerprint,
                     "column_types": {c.name: c.pg_type for c in spec.columns}})),
    )
    conn.commit()
    log.info("load  raw.%-25s %10d rows in %6.1fs", spec.table, db_rows, duration)
    return {"table": spec.table, "status": "loaded", "rows": db_rows, "duration_s": round(duration, 1)}


def ingest_all(conn: psycopg.Connection, settings: Settings, run_id: int, force: bool = False) -> list[dict]:
    cache_path = settings.data_dir.parent / "interim" / SCHEMA_CACHE
    if not cache_path.parent.exists():
        cache_path = Path(settings.data_dir) / SCHEMA_CACHE
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    sources: dict = settings.project["sources"]
    # 1) check every required file before touching the database
    specs = [build_table_spec(settings, table, cfg, cache) for table, cfg in sources.items()]
    write_json(cache_path, cache)
    # 2) load
    results = [load_table(conn, spec, run_id, force=force) for spec in specs]
    return results


def file_inventory(settings: Settings) -> list[dict]:
    """Presence/size check used by `credit-risk inventory` and the data audit notebook."""
    rows = []
    for table, cfg in settings.project["sources"].items():
        path = find_file(settings.data_dir, cfg["file"])
        rows.append({
            "table": table,
            "file": cfg["file"],
            "found": path is not None,
            "actual_name": path.name if path else None,
            "megabytes": round(path.stat().st_size / 1e6, 1) if path else None,
        })
    return rows
