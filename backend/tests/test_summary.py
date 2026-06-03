"""Tests for nightly summary recompute (db.recompute_nights).

Uses a synthetic night (2026-05-21, Amsterdam CEST UTC+2) with known values
so assertions are exact.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from healthbridge import db
from healthbridge.models import HrvSample, RhrSample, SleepSample

NIGHT = date(2026, 5, 21)

# Night window: [noon May 20 AMS = 10:00 UTC, noon May 21 AMS = 10:00 UTC)
#
# Samples (all start_ts within the window):
#   InBed:       20:00Z–04:00Z  (enclosing span)
#   AsleepCore:  20:30Z–21:30Z  3 600 s
#   AsleepREM:   21:30Z–23:00Z  5 400 s
#   Awake:       23:00Z–23:30Z  1 800 s
#   AsleepDeep:  23:30Z–03:30Z  14 400 s
#
# Expected:
#   bed_time          = 2026-05-20T20:00:00Z
#   wake_time         = 2026-05-21T04:00:00Z
#   time_in_bed       = 28 800 s  (8 h)
#   asleep            = 23 400 s  (Core + REM + Deep)
#   rem               = 5 400 s
#   deep              = 14 400 s
#   core              = 3 600 s
#   awake             = 1 800 s
#   efficiency_pct    = 23400/28800*100 = 81.25 %
#   hrv_avg_ms        = (34.2 + 38.6) / 2 = 36.4
#   rhr_bpm           = 55.0

_SRC = "Apple Watch test"

SLEEP_SAMPLES = [
    SleepSample(
        start="2026-05-20T20:00:00Z", end="2026-05-21T04:00:00Z", stage="InBed", source=_SRC
    ),
    SleepSample(
        start="2026-05-20T20:30:00Z", end="2026-05-20T21:30:00Z", stage="AsleepCore", source=_SRC
    ),
    SleepSample(
        start="2026-05-20T21:30:00Z", end="2026-05-20T23:00:00Z", stage="AsleepREM", source=_SRC
    ),
    SleepSample(
        start="2026-05-20T23:00:00Z", end="2026-05-20T23:30:00Z", stage="Awake", source=_SRC
    ),
    SleepSample(
        start="2026-05-20T23:30:00Z", end="2026-05-21T03:30:00Z", stage="AsleepDeep", source=_SRC
    ),
]

HRV_SAMPLES = [
    HrvSample(timestamp="2026-05-20T21:00:00Z", value_ms=34.2, source=_SRC),
    HrvSample(timestamp="2026-05-21T01:00:00Z", value_ms=38.6, source=_SRC),
]

RHR_SAMPLE = RhrSample(date=date(2026, 5, 21), value_bpm=55.0, source=_SRC)


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(str(Path(tmp) / "test.duckdb"))
        db.init_schema(c)
        yield c
        c.close()


def _seed(conn, *, sleep: bool = True, hrv: bool = True, rhr: bool = True) -> None:
    if sleep:
        db.insert_sleep(conn, SLEEP_SAMPLES)
    if hrv:
        db.insert_hrv(conn, HRV_SAMPLES)
    if rhr:
        db.insert_rhr(conn, [RHR_SAMPLE])


def test_recompute_basic_night(conn):
    _seed(conn)
    nights = db.recompute_nights(conn, {NIGHT})
    assert nights == [NIGHT]

    row = conn.execute(
        """SELECT time_in_bed_seconds, asleep_seconds, rem_seconds, deep_seconds,
                  core_seconds, awake_seconds, efficiency_pct, hrv_avg_ms, rhr_bpm
           FROM nightly_summary WHERE night_date = ?""",
        [NIGHT],
    ).fetchone()
    assert row is not None
    tib, asleep, rem, deep, core, awake, eff, hrv, rhr = row

    assert tib == 28800
    assert asleep == 23400
    assert rem == 5400
    assert deep == 14400
    assert core == 3600
    assert awake == 1800
    assert eff == pytest.approx(81.25, abs=0.01)
    assert hrv == pytest.approx(36.4, abs=0.01)
    assert rhr == pytest.approx(55.0)


def test_recompute_no_sleep_data(conn):
    """No sleep samples for a night -> night skipped, no row inserted, no crash."""
    nights = db.recompute_nights(conn, {NIGHT})
    assert nights == []
    assert conn.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0] == 0


def test_efficiency_zero_time_in_bed(conn):
    """Single zero-duration sample would cause div-by-zero — guard handles it."""
    # Insert a degenerate sample where start == end (0-second duration).
    # time_in_bed = 0 -> efficiency must be NULL, not a crash.
    db.insert_sleep(
        conn,
        [
            SleepSample(
                start="2026-05-20T22:00:00Z",
                end="2026-05-20T22:00:00Z",
                stage="AsleepCore",
                source=_SRC,
            )
        ],
    )
    db.recompute_nights(conn, {NIGHT})
    row = conn.execute(
        "SELECT efficiency_pct FROM nightly_summary WHERE night_date = ?", [NIGHT]
    ).fetchone()
    assert row is not None
    assert row[0] is None


def test_hrv_avg_no_samples(conn):
    """Night with sleep but no HRV -> hrv_avg_ms is NULL, not a crash."""
    _seed(conn, hrv=False)
    db.recompute_nights(conn, {NIGHT})
    hrv = conn.execute(
        "SELECT hrv_avg_ms FROM nightly_summary WHERE night_date = ?", [NIGHT]
    ).fetchone()[0]
    assert hrv is None


def test_rhr_absent(conn):
    """Night with sleep but no RHR -> rhr_bpm is NULL."""
    _seed(conn, rhr=False)
    db.recompute_nights(conn, {NIGHT})
    rhr = conn.execute(
        "SELECT rhr_bpm FROM nightly_summary WHERE night_date = ?", [NIGHT]
    ).fetchone()[0]
    assert rhr is None


def test_recompute_idempotent(conn):
    """Calling recompute twice leaves exactly one nightly_summary row."""
    _seed(conn)
    db.recompute_nights(conn, {NIGHT})
    db.recompute_nights(conn, {NIGHT})
    count = conn.execute(
        "SELECT COUNT(*) FROM nightly_summary WHERE night_date = ?", [NIGHT]
    ).fetchone()[0]
    assert count == 1


def test_recompute_multiple_nights(conn):
    """Two distinct nights produce two summary rows."""
    night2 = date(2026, 5, 22)
    samples_n2 = [
        SleepSample(
            start="2026-05-21T21:00:00Z",
            end="2026-05-22T05:00:00Z",
            stage="AsleepCore",
            source=_SRC,
        ),
    ]
    _seed(conn)
    db.insert_sleep(conn, samples_n2)
    db.recompute_nights(conn, {NIGHT, night2})
    count = conn.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0]
    assert count == 2
