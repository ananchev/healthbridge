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
    """Insert sleep samples idempotently. Return count of new rows."""
    if not samples:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0]
    conn.executemany(
        """INSERT INTO sleep_samples (start_ts, end_ts, stage, source, source_version)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT DO NOTHING""",
        [(s.start, s.end, s.stage, s.source, s.source_version) for s in samples],
    )
    after = conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0]
    return after - before


def insert_hrv(conn: duckdb.DuckDBPyConnection, samples: list[HrvSample]) -> int:
    """Insert HRV samples idempotently. PK (ts, source)."""
    if not samples:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0]
    conn.executemany(
        """INSERT INTO hrv_samples (ts, value_ms, source)
           VALUES (?, ?, ?)
           ON CONFLICT DO NOTHING""",
        [(s.timestamp, s.value_ms, s.source) for s in samples],
    )
    after = conn.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0]
    return after - before


def insert_rhr(conn: duckdb.DuckDBPyConnection, samples: list[RhrSample]) -> int:
    """Insert resting-HR samples idempotently. PK (date, source)."""
    if not samples:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0]
    conn.executemany(
        """INSERT INTO rhr_samples (date, value_bpm, source)
           VALUES (?, ?, ?)
           ON CONFLICT DO NOTHING""",
        [(s.date, s.value_bpm, s.source) for s in samples],
    )
    after = conn.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0]
    return after - before


# --- Nightly summary recompute --------------------------------------------


def affected_nights_from_sleep(samples: list[SleepSample]) -> set[date]:
    """Return the set of nights touched by a batch of sleep samples."""
    return {assign_to_night(s.start) for s in samples}


def night_window_utc(night_date: date) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) = [noon(night_date-1) AMS, noon(night_date) AMS) in UTC."""
    from datetime import timedelta

    d_prev = night_date - timedelta(days=1)
    start_local = datetime(d_prev.year, d_prev.month, d_prev.day, 12, 0, 0, tzinfo=LOCAL_TZ)
    end_local = datetime(
        night_date.year, night_date.month, night_date.day, 12, 0, 0, tzinfo=LOCAL_TZ
    )
    utc = ZoneInfo("UTC")
    return start_local.astimezone(utc), end_local.astimezone(utc)


def recompute_nights(conn: duckdb.DuckDBPyConnection, nights: set[date]) -> list[date]:
    """Recompute nightly_summary rows for the given nights.

    For each night: delete existing row, aggregate from raw tables, reinsert.
    Returns list of nights actually computed (skips nights with no sleep data).
    """
    recomputed: list[date] = []
    for night_date in sorted(nights):
        start_utc, end_utc = night_window_utc(night_date)

        sleep_row = conn.execute(
            """
            SELECT
                MIN(start_ts),
                MAX(end_ts),
                CAST(epoch(MAX(end_ts)) - epoch(MIN(start_ts)) AS INTEGER),
                CAST(COALESCE(SUM(CASE
                    WHEN stage IN ('AsleepCore','AsleepDeep','AsleepREM','AsleepUnspecified')
                    THEN epoch(end_ts) - epoch(start_ts) ELSE 0 END), 0) AS INTEGER),
                CAST(COALESCE(SUM(CASE WHEN stage = 'AsleepREM'
                    THEN epoch(end_ts) - epoch(start_ts) ELSE 0 END), 0) AS INTEGER),
                CAST(COALESCE(SUM(CASE WHEN stage = 'AsleepDeep'
                    THEN epoch(end_ts) - epoch(start_ts) ELSE 0 END), 0) AS INTEGER),
                CAST(COALESCE(SUM(CASE WHEN stage = 'AsleepCore'
                    THEN epoch(end_ts) - epoch(start_ts) ELSE 0 END), 0) AS INTEGER),
                CAST(COALESCE(SUM(CASE WHEN stage = 'Awake'
                    THEN epoch(end_ts) - epoch(start_ts) ELSE 0 END), 0) AS INTEGER)
            FROM sleep_samples
            WHERE start_ts >= ? AND start_ts < ?
            """,
            [start_utc, end_utc],
        ).fetchone()

        bed_time = sleep_row[0]
        if bed_time is None:
            continue

        wake_time = sleep_row[1]
        time_in_bed = sleep_row[2]
        asleep = sleep_row[3]
        rem = sleep_row[4]
        deep = sleep_row[5]
        core = sleep_row[6]
        awake = sleep_row[7]

        efficiency = (asleep / time_in_bed * 100) if time_in_bed and time_in_bed > 0 else None

        hrv_row = conn.execute(
            "SELECT AVG(value_ms) FROM hrv_samples WHERE ts >= ? AND ts < ?",
            [start_utc, end_utc],
        ).fetchone()
        hrv_avg = hrv_row[0] if hrv_row and hrv_row[0] is not None else None

        rhr_row = conn.execute(
            "SELECT value_bpm FROM rhr_samples WHERE date = ? LIMIT 1",
            [night_date],
        ).fetchone()
        rhr = rhr_row[0] if rhr_row else None

        conn.execute("DELETE FROM nightly_summary WHERE night_date = ?", [night_date])
        conn.execute(
            """INSERT INTO nightly_summary (
                night_date, bed_time, wake_time, time_in_bed_seconds,
                asleep_seconds, rem_seconds, deep_seconds, core_seconds, awake_seconds,
                efficiency_pct, hrv_avg_ms, rhr_bpm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                night_date,
                bed_time,
                wake_time,
                time_in_bed,
                asleep,
                rem,
                deep,
                core,
                awake,
                efficiency,
                hrv_avg,
                rhr,
            ],
        )
        recomputed.append(night_date)

    return recomputed
