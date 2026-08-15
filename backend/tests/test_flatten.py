"""Tests for overlap flattening (db.flatten_segments).

Real exports contain overlapping sleep samples for two distinct reasons:

  1. The SAME night re-arrives under a different `source` string (device rename,
     new watch). `source` is part of the sleep primary key, so both copies are
     stored — summing durations double-counts and pushes efficiency over 100 %.
  2. Apple REVISES a night: a later export re-splits segments or shifts a
     boundary by a second. Those rows differ in `end_ts`, so they are new
     primary keys too, and again overlap the originals.

Flattening resolves both: the night is collapsed to a non-overlapping timeline
where each instant carries exactly one stage. Conflicts are won by the most
recently ingested sample (Apple's latest word), then by the shorter (more
specific) sample, then by stage specificity — a total order, so the result is
deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from healthbridge import db


def _dt(hour: int, minute: int = 0, day: int = 20) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=UTC)


ING_OLD = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
ING_NEW = datetime(2026, 5, 21, 13, 0, tzinfo=UTC)


def _secs(segments, stage: str) -> float:
    return sum((s.end - s.start).total_seconds() for s in segments if s.stage == stage)


def test_flatten_empty_input():
    assert db.flatten_segments([]) == []


def test_flatten_drops_zero_length_samples():
    rows = [(_dt(22), _dt(22), "AsleepCore", ING_OLD)]
    assert db.flatten_segments(rows) == []


def test_flatten_passes_through_contiguous_segments():
    """The normal case: watch-emitted segments already tile the night."""
    rows = [
        (_dt(22), _dt(23), "AsleepCore", ING_OLD),
        (_dt(23), _dt(23, 30), "AsleepREM", ING_OLD),
    ]
    flat = db.flatten_segments(rows)
    assert [(s.start, s.end, s.stage) for s in flat] == [
        (_dt(22), _dt(23), "AsleepCore"),
        (_dt(23), _dt(23, 30), "AsleepREM"),
    ]


def test_flatten_merges_adjacent_same_stage():
    """Two touching samples of one stage collapse into a single segment."""
    rows = [
        (_dt(22), _dt(23), "AsleepCore", ING_OLD),
        (_dt(23), _dt(23, 30), "AsleepCore", ING_OLD),
    ]
    flat = db.flatten_segments(rows)
    assert len(flat) == 1
    assert (flat[0].start, flat[0].end) == (_dt(22), _dt(23, 30))


def test_flatten_dedupes_identical_segments_from_two_sources():
    """Cause 1: same interval ingested twice under different source names."""
    rows = [
        (_dt(22), _dt(23), "AsleepCore", ING_OLD),  # source "Apple Watch"
        (_dt(22), _dt(23), "AsleepCore", ING_NEW),  # source "AntonU2"
    ]
    flat = db.flatten_segments(rows)
    assert len(flat) == 1
    assert _secs(flat, "AsleepCore") == 3600


def test_flatten_revision_latest_ingest_wins():
    """Cause 2: a later export re-splits one REM block into REM/Core/REM.

    The old 30-minute REM block must not survive alongside the new split, and
    the 10 minutes Apple re-classified as Core must read as Core.
    """
    rows = [
        (_dt(22), _dt(22, 30), "AsleepREM", ING_OLD),
        (_dt(22), _dt(22, 10), "AsleepREM", ING_NEW),
        (_dt(22, 10), _dt(22, 20), "AsleepCore", ING_NEW),
        (_dt(22, 20), _dt(22, 30), "AsleepREM", ING_NEW),
    ]
    flat = db.flatten_segments(rows)
    assert _secs(flat, "AsleepCore") == 600
    assert _secs(flat, "AsleepREM") == 1200


def test_flatten_covers_wall_clock_exactly_once():
    """Total flattened time equals the wall-clock span — no double counting."""
    rows = [
        (_dt(22), _dt(23), "AsleepCore", ING_OLD),
        (_dt(22), _dt(23), "AsleepCore", ING_NEW),
        (_dt(22, 30), _dt(22, 40), "Awake", ING_NEW),
    ]
    flat = db.flatten_segments(rows)
    total = sum((s.end - s.start).total_seconds() for s in flat)
    assert total == 3600


def test_flatten_enclosing_inbed_yields_specific_stages():
    """An InBed sample enclosing the night is a container, not a competitor.

    Same ingest time, so the tiebreak decides: the shorter (more specific)
    sample wins where it overlaps, and InBed survives only in the gaps.
    """
    rows = [
        (_dt(22), _dt(23), "InBed", ING_OLD),
        (_dt(22, 15), _dt(22, 45), "AsleepDeep", ING_OLD),
    ]
    flat = db.flatten_segments(rows)
    assert _secs(flat, "AsleepDeep") == 1800
    assert _secs(flat, "InBed") == 1800  # 22:00-22:15 + 22:45-23:00


def test_flatten_is_deterministic_on_full_tie():
    """Identical span and ingest time, conflicting stage -> stable resolution."""
    rows_a = [
        (_dt(22), _dt(23), "AsleepCore", ING_OLD),
        (_dt(22), _dt(23), "Awake", ING_OLD),
    ]
    rows_b = list(reversed(rows_a))
    assert db.flatten_segments(rows_a) == db.flatten_segments(rows_b)


def test_flatten_leaves_gaps_between_episodes():
    """A nap followed by a real night stays two separate runs of segments."""
    rows = [
        (_dt(14), _dt(15), "AsleepUnspecified", ING_OLD),
        (_dt(23), _dt(23, 30), "AsleepCore", ING_OLD),
    ]
    flat = db.flatten_segments(rows)
    assert len(flat) == 2
    assert flat[1].start - flat[0].end == timedelta(hours=8)


# --- Episode splitting -------------------------------------------------------
#
# A noon-to-noon window is not the same thing as a night: it can also contain
# naps. Splitting on a gap is safe because the watch emits contiguous segments
# (including Awake) while it is recording — across all real data the gap between
# consecutive segments is either exactly 0 or larger than two hours, so any
# threshold in that empty band gives the same answer.


def test_split_episodes_single_contiguous_run():
    segs = db.flatten_segments(
        [
            (_dt(22), _dt(23), "AsleepCore", ING_OLD),
            (_dt(23), _dt(23, 30), "AsleepREM", ING_OLD),
        ]
    )
    assert len(db.split_episodes(segs)) == 1


def test_split_episodes_splits_on_long_gap():
    segs = db.flatten_segments(
        [
            (_dt(14), _dt(15), "AsleepUnspecified", ING_OLD),  # nap
            (_dt(23), _dt(23, 30), "AsleepCore", ING_OLD),  # night
        ]
    )
    episodes = db.split_episodes(segs)
    assert len(episodes) == 2
    assert episodes[0][0].start == _dt(14)
    assert episodes[1][0].start == _dt(23)


def test_split_episodes_keeps_short_gap_together():
    """A brief non-recording gap mid-night is still the same sleep opportunity."""
    segs = db.flatten_segments(
        [
            (_dt(22), _dt(23), "AsleepCore", ING_OLD),
            (_dt(23, 30), _dt(23, 45), "AsleepCore", ING_OLD),
        ]
    )
    assert len(db.split_episodes(segs)) == 1


def test_split_episodes_gap_threshold_is_exclusive():
    """A gap exactly at the threshold stays one episode; one second more splits."""
    segs = db.flatten_segments(
        [
            (_dt(22), _dt(23), "AsleepCore", ING_OLD),
            (_dt(0, 0, day=21), _dt(0, 30, day=21), "AsleepCore", ING_OLD),
        ]
    )
    assert len(db.split_episodes(segs, gap_seconds=3600)) == 1
    assert len(db.split_episodes(segs, gap_seconds=3599)) == 2


def test_split_episodes_empty():
    assert db.split_episodes([]) == []


def test_main_episode_is_the_one_with_most_sleep():
    """Not the longest span: a restless 3 h nap must not outrank a solid night."""
    nap = db.flatten_segments(
        [
            (_dt(13), _dt(14), "AsleepUnspecified", ING_OLD),
            (_dt(14), _dt(16), "Awake", ING_OLD),
        ]
    )
    night = db.flatten_segments([(_dt(23), _dt(1, day=21), "AsleepCore", ING_OLD)])
    episodes = db.split_episodes(nap + night)
    assert len(episodes) == 2
    assert db.main_episode(episodes)[0].start == _dt(23)
