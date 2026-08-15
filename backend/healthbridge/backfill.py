"""One-shot rebuild of every nightly_summary row from the raw samples.

`nightly_summary` is derived data. When the way it is derived changes — overlap
flattening, episode splitting, nap columns — every row written by the older
logic is stale, and ordinary ingest only ever recomputes the nights a new batch
touches. This module recomputes all of them.

It writes nothing but `nightly_summary`: the raw sample tables are the source of
truth and are read, never modified. Safe to re-run.

Run it against the deployed database (the service opens a connection per request
rather than holding the write lock, so it does not need to be stopped):

    docker exec healthbridge-backend python -m healthbridge.backfill
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import duckdb

from . import db


def all_nights(conn: duckdb.DuckDBPyConnection) -> set[date]:
    """Every night that has raw sleep data, using the canonical night rule."""
    rows = conn.execute("SELECT DISTINCT start_ts FROM sleep_samples").fetchall()
    return {db.assign_to_night(r[0]) for r in rows}


def backfill(conn: duckdb.DuckDBPyConnection) -> list[date]:
    """Recompute every night's summary. Returns the nights rebuilt, in order."""
    return db.recompute_nights(conn, all_nights(conn))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=os.environ.get("HEALTHBRIDGE_DB", "/data/health.duckdb"),
        help="path to health.duckdb (default: $HEALTHBRIDGE_DB)",
    )
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    try:
        db.init_schema(conn)  # adds any columns this build expects
        nights = backfill(conn)
    finally:
        conn.close()

    if not nights:
        print(f"no sleep data in {args.db} — nothing to rebuild")
        return 0
    print(f"rebuilt {len(nights)} nights ({nights[0]} .. {nights[-1]}) in {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
