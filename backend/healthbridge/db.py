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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from .models import ASLEEP_STAGES, HrvSample, RhrSample, SleepSample

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

# Gap that separates one sleep episode (a night, a nap) from the next. See
# split_episodes for why any value in the 5-120 min range gives the same answer
# on real data.
EPISODE_GAP_SECONDS = 3600

# Stage specificity, used ONLY as a last-resort tiebreak when two samples cover
# the same instant with the same ingest time and the same duration. Higher wins,
# so a graded sleep stage beats generic "asleep", which beats the InBed container.
_STAGE_RANK = {
    "InBed": 0,
    "Awake": 1,
    "AsleepUnspecified": 2,
    "AsleepCore": 3,
    "AsleepREM": 4,
    "AsleepDeep": 5,
}


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


# --- Overlap flattening -----------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """One stretch of wall-clock time carrying exactly one stage."""

    start: datetime
    end: datetime
    stage: str


def flatten_segments(
    rows: Iterable[tuple[datetime, datetime, str, datetime]],
) -> list[Segment]:
    """Collapse possibly-overlapping sleep samples into a non-overlapping timeline.

    Raw samples overlap for two reasons: the same night re-arrives under a new
    `source` (device rename — both copies stored, since source is part of the
    PK), and Apple revises a night in a later export (re-split segments, ±1 s
    boundary shifts — new PKs again). Summing sample durations therefore
    double-counts; flattening measures wall-clock time instead, which is what
    "asleep" and "in bed" actually mean.

    `rows` are (start_ts, end_ts, stage, ingested_at). The timeline is cut at
    every sample boundary; each resulting atomic interval is awarded to the
    covering sample with, in order: the latest `ingested_at` (Apple's most
    recent word), then the shortest duration (a re-split segment is more
    specific than the block it replaces, and an enclosing InBed sample never
    masks a stage), then the highest stage rank. That is a total order, so the
    output does not depend on row order.

    Adjacent intervals sharing a stage are merged. Zero-length samples are
    dropped. Gaps between samples stay gaps — episode splitting is a separate
    concern.
    """
    samples = [(s, e, stage, ing) for s, e, stage, ing in rows if e > s]
    if not samples:
        return []

    bounds = sorted({t for s, e, _, _ in samples for t in (s, e)})
    out: list[Segment] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        covering = [
            (ing, -(e - s).total_seconds(), _STAGE_RANK.get(stage, -1), stage)
            for s, e, stage, ing in samples
            if s <= a and e >= b
        ]
        if not covering:
            continue  # a gap between episodes
        stage = max(covering)[3]
        if out and out[-1].stage == stage and out[-1].end == a:
            out[-1] = Segment(out[-1].start, b, stage)
        else:
            out.append(Segment(a, b, stage))
    return out


def stage_seconds(segments: Iterable[Segment]) -> dict[str, int]:
    """Total seconds per stage over a flattened timeline."""
    totals: dict[str, float] = {}
    for seg in segments:
        totals[seg.stage] = totals.get(seg.stage, 0.0) + (seg.end - seg.start).total_seconds()
    return {stage: int(secs) for stage, secs in totals.items()}


def asleep_seconds(segments: Iterable[Segment]) -> int:
    """Total seconds in any asleep stage over a flattened timeline."""
    return sum(
        int((seg.end - seg.start).total_seconds()) for seg in segments if seg.stage in ASLEEP_STAGES
    )


# --- Episode splitting ------------------------------------------------------


def split_episodes(
    segments: list[Segment], *, gap_seconds: int = EPISODE_GAP_SECONDS
) -> list[list[Segment]]:
    """Split a flattened timeline into separate sleep episodes on long gaps.

    A noon-to-noon window is a *window*, not a night: it can also hold naps.
    Measuring bed/wake/time-in-bed across the whole window makes a nap set the
    bed time and counts the hours awake between nap and night as "in bed"
    (real example: a 14:38 nap turned 2026-07-19 into 20h35m in bed at 52 %
    efficiency).

    Splitting on a gap is safe because the watch emits contiguous segments
    (including Awake) for as long as it is recording: across all real data the
    gap between consecutive segments is either exactly 0 or more than two hours.
    A gap of exactly `gap_seconds` still counts as one episode; anything longer
    starts a new one.
    """
    episodes: list[list[Segment]] = []
    current: list[Segment] = []
    for seg in segments:
        if current and (seg.start - current[-1].end).total_seconds() > gap_seconds:
            episodes.append(current)
            current = []
        current.append(seg)
    if current:
        episodes.append(current)
    return episodes


def main_episode(episodes: list[list[Segment]]) -> list[Segment]:
    """Pick the night from a set of episodes: the one with the most sleep.

    Deliberately not the longest span — a restless 3 h nap must not outrank a
    compact 5 h night. Ties break on span, then on the later start, so the
    choice is deterministic.
    """
    return max(
        episodes,
        key=lambda ep: (
            asleep_seconds(ep),
            (ep[-1].end - ep[0].start).total_seconds(),
            ep[0].start,
        ),
    )


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

        rows = conn.execute(
            """SELECT start_ts, end_ts, stage, ingested_at FROM sleep_samples
               WHERE start_ts >= ? AND start_ts < ?""",
            [start_utc, end_utc],
        ).fetchall()

        if not rows:
            continue

        # Raw samples may overlap (device rename, Apple revisions) — flatten to a
        # non-overlapping timeline before measuring anything. See flatten_segments.
        segments = flatten_segments(rows)
        # The window can hold naps as well as the night; measure the night only.
        episodes = split_episodes(segments)
        night = main_episode(episodes) if episodes else []
        by_stage = stage_seconds(night)

        if night:
            bed_time = night[0].start
            wake_time = night[-1].end
        else:
            # Only zero-length samples: keep the row (efficiency guard covers it).
            bed_time = min(r[0] for r in rows)
            wake_time = max(r[1] for r in rows)
        time_in_bed = int((wake_time - bed_time).total_seconds())
        asleep = asleep_seconds(night)
        rem = by_stage.get("AsleepREM", 0)
        deep = by_stage.get("AsleepDeep", 0)
        core = by_stage.get("AsleepCore", 0)
        awake = by_stage.get("Awake", 0)

        # Sleep outside the night is a nap: real sleep that belongs in the daily
        # total, but not in the night's efficiency or stage architecture.
        naps = [ep for ep in episodes if ep is not night]
        nap_secs = sum(asleep_seconds(ep) for ep in naps)
        total_asleep = asleep + nap_secs

        efficiency = (asleep / time_in_bed * 100) if time_in_bed and time_in_bed > 0 else None

        # One value per timestamp: a reading re-delivered under a second source
        # name (or revised by a later export) must not be averaged with itself.
        hrv_row = conn.execute(
            """SELECT AVG(value_ms) FROM (
                   SELECT DISTINCT ON (ts) value_ms FROM hrv_samples
                   WHERE ts >= ? AND ts < ?
                   ORDER BY ts, ingested_at DESC, source
               ) AS latest""",
            [start_utc, end_utc],
        ).fetchone()
        hrv_avg = hrv_row[0] if hrv_row and hrv_row[0] is not None else None

        # Same day can hold two values that genuinely differ (Apple revises RHR);
        # take the most recent export rather than an arbitrary row.
        rhr_row = conn.execute(
            "SELECT value_bpm FROM rhr_samples WHERE date = ? "
            "ORDER BY ingested_at DESC, source LIMIT 1",
            [night_date],
        ).fetchone()
        rhr = rhr_row[0] if rhr_row else None

        conn.execute("DELETE FROM nightly_summary WHERE night_date = ?", [night_date])
        conn.execute(
            """INSERT INTO nightly_summary (
                night_date, bed_time, wake_time, time_in_bed_seconds,
                asleep_seconds, rem_seconds, deep_seconds, core_seconds, awake_seconds,
                efficiency_pct, hrv_avg_ms, rhr_bpm,
                nap_seconds, nap_count, total_asleep_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                nap_secs,
                len(naps),
                total_asleep,
            ],
        )
        recomputed.append(night_date)

    return recomputed
