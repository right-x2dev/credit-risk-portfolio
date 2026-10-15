"""Thin PostgreSQL helpers (psycopg 3).

Large result sets are moved with COPY instead of row-by-row fetches so that pandas never has
to materialise Python objects per cell.
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql

from .config import Settings, get_settings

log = logging.getLogger(__name__)


@contextmanager
def connect(settings: Settings | None = None, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    settings = settings or get_settings()
    conn = psycopg.connect(settings.conninfo, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def wait_for_db(settings: Settings | None = None, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with connect(settings, autocommit=True) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:  # pragma: no cover - timing dependent
            last_err = exc
            time.sleep(2)
    raise RuntimeError(f"PostgreSQL not reachable within {timeout_s}s: {last_err}")


def ensure_database(settings: Settings) -> None:
    """Create settings.pg_db if missing (used by tests that run on a separate database)."""
    admin = settings.conninfo.replace(f"dbname={settings.pg_db}", "dbname=postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (settings.pg_db,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(settings.pg_db)))


def run_sql_file(conn: psycopg.Connection, path: Path, params: dict[str, Any] | None = None) -> float:
    """Execute a SQL script. `{{name}}` placeholders are substituted from params (identifiers/literals
    controlled by config only, never user input)."""
    text = path.read_text(encoding="utf-8")
    for key, value in (params or {}).items():
        text = text.replace("{{" + key + "}}", str(value))
    start = time.perf_counter()
    conn.execute(text)
    elapsed = time.perf_counter() - start
    log.info("sql %-55s %6.1fs", f"{path.parent.name}/{path.name}", elapsed)
    return elapsed


def run_sql_dir(conn: psycopg.Connection, directory: Path, params: dict[str, Any] | None = None) -> None:
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no SQL files in {directory}")
    for path in files:
        run_sql_file(conn, path, params)


def scalar(conn: psycopg.Connection, query: str, params: Iterable[Any] | None = None) -> Any:
    row = conn.execute(query, params).fetchone()
    return None if row is None else row[0]


def read_frame(conn: psycopg.Connection, query: str, **read_csv_kwargs: Any) -> pd.DataFrame:
    """Read a query result via COPY ... TO STDOUT (much faster than cursor fetch for wide tables)."""
    buf = io.BytesIO()
    with conn.cursor().copy(f"COPY ({query}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
        for chunk in copy:
            buf.write(chunk)
    buf.seek(0)
    if buf.getbuffer().nbytes == 0:
        return pd.DataFrame()
    return pd.read_csv(buf, low_memory=False, **read_csv_kwargs)


_PG_TYPES = {
    "int64": "bigint",
    "Int64": "bigint",
    "int32": "integer",
    "int8": "smallint",
    "float64": "double precision",
    "float32": "double precision",
    "bool": "boolean",
    "boolean": "boolean",
    "datetime64[ns]": "timestamp",
    "datetime64[ns, UTC]": "timestamptz",
}


def write_frame(
    conn: psycopg.Connection,
    df: pd.DataFrame,
    table: str,
    *,
    replace: bool = True,
    primary_key: list[str] | None = None,
) -> int:
    """Create (or replace) `schema.table` from a DataFrame and bulk load it with COPY."""
    schema, name = table.split(".")
    ident = sql.Identifier(schema, name)
    cols = []
    for col, dtype in df.dtypes.items():
        pg_type = _PG_TYPES.get(str(dtype), "text")
        cols.append(sql.SQL("{} {}").format(sql.Identifier(col), sql.SQL(pg_type)))
    if primary_key:
        cols.append(sql.SQL("PRIMARY KEY ({})").format(sql.SQL(", ").join(map(sql.Identifier, primary_key))))
    if replace:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(ident))
    conn.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(ident, sql.SQL(", ").join(cols)))

    out = io.StringIO()
    frame = df.copy()
    for col in frame.columns:
        if frame[col].dtype == object:
            frame[col] = frame[col].map(lambda v: json.dumps(v) if isinstance(v, (dict, list)) else v)
    frame.to_csv(out, index=False, header=False, na_rep="")
    out.seek(0)
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(
        ident, sql.SQL(", ").join(map(sql.Identifier, df.columns))
    )
    with conn.cursor().copy(copy_sql) as copy:
        while data := out.read(1 << 20):
            copy.write(data)
    return len(df)
