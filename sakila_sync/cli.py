import argparse
import logging
import sys
from datetime import date
from dotenv import load_dotenv
from sqlalchemy import func, select
from sakila_sync.db import mysql_session, sqlite_session
from sakila_sync.models.analytics import AnalyticsBase, DimDate, SyncState
from sakila_sync.sync.full_load import SOURCE_MODELS, _load_dim_date, full_load
from sakila_sync.sync.incremental import incremental
from sakila_sync.sync.validate import resolve_period, validate

log = logging.getLogger("sakila_sync")

def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date '{value}', expected YYYY-MM-DD")

def _sessions(args: argparse.Namespace):
    test = getattr(args, "test", False)
    return mysql_session(test), sqlite_session(test)

def cmd_init(args: argparse.Namespace) -> int:
    src, dst = _sessions(args)
    try:
        src.execute(select(1))
        AnalyticsBase.metadata.create_all(dst.get_bind())

        existing = set(dst.scalars(select(SyncState.source_table)))
        for model in SOURCE_MODELS:
            if model.__tablename__ not in existing:
                dst.add(SyncState(source_table=model.__tablename__, last_update=None))

        if dst.scalar(select(func.count()).select_from(DimDate)) == 0:
            _load_dim_date(src, dst)
        dst.commit()
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()
    print("init OK: MySQL reachable, analytics tables created, dim_date and sync_state prepared.")
    return 0

def cmd_full_load(args: argparse.Namespace) -> int:
    src, dst = _sessions(args)
    try:
        counts = full_load(src, dst)
    finally:
        src.close()
        dst.close()
    print("full-load OK:")
    for table, n in counts.items():
        print(f"  {table:<22} {n} rows")
    return 0

def cmd_incremental(args: argparse.Namespace) -> int:
    src, dst = _sessions(args)
    try:
        stats = incremental(src, dst)
    finally:
        src.close()
        dst.close()
    print("incremental OK:")
    if not stats:
        print("  no changes since last sync")
    for table, c in stats.items():
        print(f"  {table:<22} inserted={c['inserted']} updated={c['updated']}")
    return 0

def cmd_validate(args: argparse.Namespace) -> int:
    src, dst = _sessions(args)
    try:
        start, end = resolve_period(src, args.date_from, args.date_to)
        errors = validate(src, dst, start, end)
    finally:
        src.close()
        dst.close()
    if errors:
        print(f"validate FAILED for {start}..{end} with {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"validate OK for {start}..{end}: counts, totals and keys all match.")
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sakila_sync")
    parser.add_argument("--test", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("full-load").set_defaults(func=cmd_full_load)
    sub.add_parser("incremental").set_defaults(func=cmd_incremental)

    p_val = sub.add_parser("validate")
    p_val.add_argument("--from", dest="date_from", type=_iso_date)
    p_val.add_argument("--to", dest="date_to", type=_iso_date)
    p_val.set_defaults(func=cmd_validate)

    return parser

def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except NotImplementedError:
        print(f"Error: '{args.command}' is not implemented yet.", file=sys.stderr)
        return 1
    except Exception as exc:
        log.exception("command '%s' failed", args.command)
        print(f"Error: {args.command} failed: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
