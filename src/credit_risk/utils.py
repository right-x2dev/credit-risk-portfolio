"""Shared helpers: logging, timing, pipeline-run audit context, hashing."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

log = logging.getLogger("credit_risk")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


@contextmanager
def timed(label: str) -> Iterator[None]:
    start = time.perf_counter()
    log.info("start %s", label)
    try:
        yield
    finally:
        log.info("done  %s in %.1fs", label, time.perf_counter() - start)


def start_run(conn: psycopg.Connection, run_type: str, data_dir: str | None = None, details: dict | None = None) -> int:
    row = conn.execute(
        "INSERT INTO audit.pipeline_run (run_type, data_dir, details) VALUES (%s, %s, %s) RETURNING run_id",
        (run_type, data_dir, json.dumps(details or {})),
    ).fetchone()
    conn.commit()
    return int(row[0])


def finish_run(conn: psycopg.Connection, run_id: int, status: str, details: dict | None = None) -> None:
    conn.execute(
        """UPDATE audit.pipeline_run
              SET finished_at = now(), status = %s,
                  details = COALESCE(details, '{}'::jsonb) || %s::jsonb
            WHERE run_id = %s""",
        (status, json.dumps(details or {}, default=str), run_id),
    )
    conn.commit()


@contextmanager
def audited_run(conn: psycopg.Connection, run_type: str, data_dir: str | None = None) -> Iterator[int]:
    """Record a pipeline run; failures are committed to audit before the exception propagates."""
    run_id = start_run(conn, run_type, data_dir)
    try:
        yield run_id
    except BaseException as exc:
        conn.rollback()
        finish_run(conn, run_id, "failed", {"error": f"{type(exc).__name__}: {exc}"[:2000]})
        raise
    else:
        conn.commit()
        finish_run(conn, run_id, "success")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"file": path.name, "bytes": stat.st_size, "mtime": int(stat.st_mtime)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
