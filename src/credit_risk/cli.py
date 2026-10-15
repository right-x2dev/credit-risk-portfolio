"""Command line entry point: `python -m credit_risk.cli <command>`.

Commands are small, re-runnable steps; `run-all` chains them. Every command returns a
non-zero exit code on failure (PowerShell scripts rely on this).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT, get_settings
from .utils import audited_run, setup_logging, timed

log = logging.getLogger("credit_risk.cli")


def cmd_init_db(args: argparse.Namespace) -> None:
    from .db import connect, run_sql_dir, wait_for_db

    settings = get_settings()
    wait_for_db(settings)
    with connect(settings) as conn:
        run_sql_dir(conn, settings.sql_dir / "00_init")
    log.info("database initialised: %s@%s:%s/%s", settings.pg_user, settings.pg_host, settings.pg_port, settings.pg_db)


def cmd_inventory(args: argparse.Namespace) -> None:
    from .ingest import file_inventory

    rows = file_inventory(get_settings())
    for r in rows:
        log.info("%-22s %-36s found=%-5s %s MB", r["table"], r["file"], r["found"], r["megabytes"])
    missing = [r["file"] for r in rows if not r["found"]]
    if missing:
        raise SystemExit(f"missing source files: {missing}. Download the Kaggle data into {get_settings().data_dir}")


def cmd_ingest(args: argparse.Namespace) -> None:
    from .db import connect
    from .ingest import ingest_all
    from .validate import raw_checks

    settings = get_settings()
    cmd_init_db(args)
    with connect(settings) as conn, audited_run(conn, "ingest", str(settings.data_dir)) as run_id:
        with timed("raw ingestion"):
            results = ingest_all(conn, settings, run_id, force=args.force)
        with timed("raw data quality"):
            q = raw_checks(conn, settings, run_id)
        log.info("raw quality summary: %s", q.summary())
        log.info("loaded: %s", json.dumps(results))
        q.raise_on_fail()


def cmd_build_staging(args: argparse.Namespace) -> None:
    from .transform import build_staging

    build_staging(get_settings())


def cmd_build_features(args: argparse.Namespace) -> None:
    from .features import build_feature_mart

    build_feature_mart(get_settings())


def cmd_eda(args: argparse.Namespace) -> None:
    from .eda import run_eda

    run_eda(get_settings())


def cmd_split(args: argparse.Namespace) -> None:
    from .split import create_split

    create_split(get_settings())


def cmd_train(args: argparse.Namespace) -> None:
    from .train import train_and_evaluate

    train_and_evaluate(get_settings())


def cmd_score(args: argparse.Namespace) -> None:
    from .score import score_population

    score_population(get_settings())


def cmd_build_mart(args: argparse.Namespace) -> None:
    from .mart import build_mart

    build_mart(get_settings())


def cmd_export_bi(args: argparse.Namespace) -> None:
    from .mart import export_powerbi_snapshot

    export_powerbi_snapshot(get_settings())


def cmd_build_pbip(args: argparse.Namespace) -> None:
    from .powerbi import build_pbip

    log.info("wrote %s", build_pbip(get_settings(), data_source=args.data_source,
                                         active_page=args.active_page))


def cmd_powerbi_validation(args: argparse.Namespace) -> None:
    from .pbi_validation import write_powerbi_validation

    passed, total = write_powerbi_validation(get_settings())
    log.info("Power BI validation: %d/%d checks match", passed, total)
    if passed != total:
        raise SystemExit(f"{total - passed} Power BI reconciliation check(s) failed")


def cmd_docs(args: argparse.Namespace) -> None:
    from .docs import write_data_dictionary, write_leakage_review

    settings = get_settings()
    log.info("wrote %s", write_data_dictionary(settings))
    log.info("wrote %s", write_leakage_review(settings))


def cmd_make_synthetic(args: argparse.Namespace) -> None:
    from .synthetic import generate

    out = Path(args.out) if Path(args.out).is_absolute() else PROJECT_ROOT / args.out
    counts = generate(out, n_train=args.n_train, n_test=args.n_test, seed=args.seed)
    log.info("synthetic data written to %s: %s", out, counts)


def cmd_run_all(args: argparse.Namespace) -> None:
    steps = [cmd_ingest, cmd_build_staging, cmd_build_features, cmd_eda, cmd_split, cmd_train, cmd_score,
             cmd_build_mart, cmd_export_bi, cmd_docs, cmd_build_pbip]
    for step in steps:
        with timed(step.__name__.replace("cmd_", "")):
            step(args)


COMMANDS = {
    "init-db": cmd_init_db,
    "inventory": cmd_inventory,
    "ingest": cmd_ingest,
    "build-staging": cmd_build_staging,
    "build-features": cmd_build_features,
    "eda": cmd_eda,
    "split": cmd_split,
    "train": cmd_train,
    "score": cmd_score,
    "build-mart": cmd_build_mart,
    "export-bi": cmd_export_bi,
    "docs": cmd_docs,
    "build-pbip": cmd_build_pbip,
    "powerbi-validation": cmd_powerbi_validation,
    "make-synthetic": cmd_make_synthetic,
    "run-all": cmd_run_all,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="credit-risk", description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--force", action="store_true", help="ingest: reload even if files are unchanged")
    parser.add_argument("--out", default="data/synthetic", help="make-synthetic: output directory")
    parser.add_argument("--n-train", type=int, default=30_000)
    parser.add_argument("--n-test", type=int, default=6_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--data-source", default="PostgreSQL", choices=["PostgreSQL", "CSV"],
                        help="build-pbip: default value of the Power BI DataSource parameter")
    parser.add_argument("--active-page", default=None, help="build-pbip: page the report opens on")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        COMMANDS[args.command](args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            log.error("%s", exc.code)
            return 1
    except Exception:
        log.exception("command %s failed", args.command)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
