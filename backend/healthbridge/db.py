"""DuckDB access layer for the ingestion service.

This module is the ONLY writer to the database. It:
  - initializes the schema (schema.sql)
  - performs idempotent inserts (ON CONFLICT DO NOTHING)
  - recomputes nightly_summary rows for affected nights

NOTE for Claude Code: function bodies are stubs with clear contracts. Implement
them. Keep ALL write logic here so bootstrap/ can reuse it (no parallel logic).

Key domain rules (see CLAUDE.md):
  - "night" = noon-to-noon (start < 12:00 local -> that date; else next date)
  - efficiency = asleep_seconds / time_in_bed_seconds * 100
  - assume Europe/Amsterdam for night assignment in v1
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from .models import HrvSample, RhrSample, SleepSample

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")


def connect(db_path: str, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection. Ingestion uses read_only=False; MCP uses True."""
    return duckdb.connect(db_path, read_only=read_only)


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Apply schema.sql. Safe to call repeatedly (CREATE IF NOT EXISTS)."""
    conn.execute(SCHEMA_PATH.read_text())


def assign_to_night(start_ts: datetime) -> date:
    """Noon-to-noon night assignment in LOCAL_TZ.

    A sample whose local start time is before 12:00 belongs to that calendar
    date's night; at/after 12:00 it belongs to the next day's night.
    """
    local = start_ts.astimezone(LOCAL_TZ)
    if local.hour < 12:
        return local.date()
    from datetime import timedelta

    return (local + timedelta(days=1)).date()


# --- Idempotent inserts ----------------------------------------------------
# All use INSERT ... ON CONFLICT DO NOTHING. Return number of NEW rows written.

def insert_sleep(conn: duckdb.DuckDBPyConnection, samples: list[SleepSample]) -> int:
    """Insert sleep samples idempotently. Return count of new rows.

    TODO(claude-code): executemany with ON CONFLICT DO NOTHING against
    sleep_samples PK (start_ts, end_ts, stage, source). Count rows actually
    inserted (compare counts before/after, or use RETURNING if supported).
    """
    raise NotImplementedError


def insert_hrv(conn: duckdb.DuckDBPyConnection, samples: list[HrvSample]) -> int:
    """Insert HRV samples idempotently. PK (ts, source)."""
    raise NotImplementedError


def insert_rhr(conn: duckdb.DuckDBPyConnection, samples: list[RhrSample]) -> int:
    """Insert resting-HR samples idempotently. PK (date, source)."""
    raise NotImplementedError


# --- Nightly summary recompute --------------------------------------------

def affected_nights_from_sleep(samples: list[SleepSample]) -> set[date]:
    """Return the set of nights touched by a batch of sleep samples."""
    return {assign_to_night(s.start) for s in samples}


def recompute_nights(conn: duckdb.DuckDBPyConnection, nights: set[date]) -> list[date]:
    """Recompute nightly_summary rows for the given nights.

    For each night: drop the existing row, recompute from raw tables, insert.
    Pure SQL preferred. Pull:
      - bed_time = min(start_ts), wake_time = max(end_ts) over the night's sleep
        samples (Apple Watch source). Night boundary via assign_to_night logic —
        implement as a SQL expression or compute per-night in Python then write.
      - per-stage seconds via SUM(epoch(end_ts) - epoch(start_ts)) grouped by stage
      - asleep_seconds = sum of ASLEEP_STAGES
      - efficiency_pct = asleep / time_in_bed * 100
      - hrv_avg_ms = avg(value_ms) of hrv_samples assigned to that night
      - rhr_bpm = rhr_samples.value_bpm for that night_date (if present)

    Return the list of nights actually recomputed.

    TODO(claude-code): implement. Watch the noon-to-noon boundary carefully — the
    simplest correct approach is to compute the [noon prev day, noon this day)
    UTC-adjusted window per night and aggregate within it.
    """
    raise NotImplementedError
