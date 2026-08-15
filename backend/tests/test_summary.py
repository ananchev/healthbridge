"""Tests for nightly summary recompute (db.recompute_nights).

Uses a synthetic night (2026-05-21, Amsterdam CEST UTC+2) with known values
so assertions are exact.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
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


_ING = "2026-05-21T10:00:00Z"  # a single export time, for rows inserted raw


def _insert_raw(conn, rows) -> None:
    """Insert sleep rows with an explicit ingested_at (which insert_sleep sets itself).

    Needed to exercise revision handling, where the winner is decided by which
    export arrived last.
    """
    conn.executemany(
        """INSERT INTO sleep_samples (start_ts, end_ts, stage, source, ingested_at)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
        rows,
    )


def test_duplicate_source_does_not_double_count(conn):
    """A device rename re-delivers the whole night under a new source name.

    Both copies are stored (source is part of the PK) — the summary must still
    report one night's worth of sleep, not two.
    """
    _seed(conn)
    db.insert_sleep(
        conn,
        [s.model_copy(update={"source": "AntonU2"}) for s in SLEEP_SAMPLES],
    )
    assert conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0] == 10

    db.recompute_nights(conn, {NIGHT})
    tib, asleep, eff = conn.execute(
        "SELECT time_in_bed_seconds, asleep_seconds, efficiency_pct "
        "FROM nightly_summary WHERE night_date = ?",
        [NIGHT],
    ).fetchone()
    assert tib == 28800
    assert asleep == 23400
    assert eff == pytest.approx(81.25, abs=0.01)


def test_revised_night_uses_latest_export(conn):
    """Apple re-splits a block in a later export; the newer version wins."""
    _insert_raw(
        conn,
        [
            # First export: one 40-minute REM block.
            (
                "2026-05-20T22:00:00Z",
                "2026-05-20T22:40:00Z",
                "AsleepREM",
                _SRC,
                "2026-05-21T10:00:00Z",
            ),
            # Second export: same span, re-split with 10 minutes reclassified Awake.
            (
                "2026-05-20T22:00:00Z",
                "2026-05-20T22:30:00Z",
                "AsleepREM",
                _SRC,
                "2026-05-21T13:00:00Z",
            ),
            ("2026-05-20T22:30:00Z", "2026-05-20T22:40:00Z", "Awake", _SRC, "2026-05-21T13:00:00Z"),
        ],
    )
    db.recompute_nights(conn, {NIGHT})
    asleep, rem, awake, eff = conn.execute(
        "SELECT asleep_seconds, rem_seconds, awake_seconds, efficiency_pct "
        "FROM nightly_summary WHERE night_date = ?",
        [NIGHT],
    ).fetchone()
    assert rem == 1800
    assert awake == 600
    assert asleep == 1800
    assert eff == pytest.approx(75.0, abs=0.01)


def test_efficiency_never_exceeds_100(conn):
    """The invariant the >100 % rows violated: asleep can never beat time in bed."""
    _seed(conn)
    db.insert_sleep(conn, [s.model_copy(update={"source": "AntonU2"}) for s in SLEEP_SAMPLES])
    _insert_raw(
        conn,
        [
            (
                "2026-05-20T22:00:00Z",
                "2026-05-20T23:05:00Z",
                "AsleepDeep",
                "third",
                "2026-05-21T14:00:00Z",
            ),
        ],
    )
    db.recompute_nights(conn, {NIGHT})
    eff = conn.execute(
        "SELECT efficiency_pct FROM nightly_summary WHERE night_date = ?", [NIGHT]
    ).fetchone()[0]
    assert eff <= 100.0


def test_stage_seconds_sum_to_time_in_bed(conn):
    """Flattened stages tile the night exactly — no gaps, no overlap."""
    _seed(conn)
    db.recompute_nights(conn, {NIGHT})
    tib, asleep, awake = conn.execute(
        "SELECT time_in_bed_seconds, asleep_seconds, awake_seconds FROM nightly_summary "
        "WHERE night_date = ?",
        [NIGHT],
    ).fetchone()
    # InBed spans 20:00-04:00 and encloses the staged samples; the 60 min it is
    # the only cover for (20:00-20:30, 03:30-04:00) is in-bed-but-unstaged time.
    assert tib == asleep + awake + 3600


# A nap earlier in the same noon-to-noon window, well separated from the night.
# Mirrors 2026-07-19: nap 14:38-16:04 local, then a 9 h gap, then real sleep.
NAP_SAMPLES = [
    SleepSample(
        start="2026-05-20T13:00:00Z",
        end="2026-05-20T14:00:00Z",
        stage="AsleepUnspecified",
        source=_SRC,
    ),
]


def test_nap_does_not_stretch_the_night(conn):
    """A daytime nap must not become the night's bed_time or inflate time in bed."""
    _seed(conn)
    db.insert_sleep(conn, NAP_SAMPLES)
    db.recompute_nights(conn, {NIGHT})

    bed, wake, tib, asleep, eff = conn.execute(
        """SELECT bed_time, wake_time, time_in_bed_seconds, asleep_seconds, efficiency_pct
           FROM nightly_summary WHERE night_date = ?""",
        [NIGHT],
    ).fetchone()

    # Night starts at 20:00Z, not at the 13:00Z nap.
    assert bed.astimezone(UTC) == datetime(2026, 5, 20, 20, 0, tzinfo=UTC)
    assert wake.astimezone(UTC) == datetime(2026, 5, 21, 4, 0, tzinfo=UTC)
    assert tib == 28800  # 8 h, not 15 h
    assert asleep == 23400  # the nap's hour is not part of the night
    assert eff == pytest.approx(81.25, abs=0.01)


def test_nap_recorded_as_its_own_metric(conn):
    """Nap sleep is excluded from the night but kept — it still counts as sleep."""
    _seed(conn)
    db.insert_sleep(conn, NAP_SAMPLES)
    db.recompute_nights(conn, {NIGHT})

    asleep, naps, nap_count, total = conn.execute(
        """SELECT asleep_seconds, nap_seconds, nap_count, total_asleep_seconds
           FROM nightly_summary WHERE night_date = ?""",
        [NIGHT],
    ).fetchone()
    assert asleep == 23400
    assert naps == 3600
    assert nap_count == 1
    assert total == 27000


def test_night_without_naps_reports_zero(conn):
    """No second episode -> nap columns are 0, and total equals the night."""
    _seed(conn)
    db.recompute_nights(conn, {NIGHT})
    asleep, naps, nap_count, total = conn.execute(
        """SELECT asleep_seconds, nap_seconds, nap_count, total_asleep_seconds
           FROM nightly_summary WHERE night_date = ?""",
        [NIGHT],
    ).fetchone()
    assert (naps, nap_count) == (0, 0)
    assert total == asleep


def test_nap_awake_time_is_not_counted_as_nap_sleep(conn):
    """Only asleep time in the other episodes counts — lying awake does not."""
    _insert_raw(
        conn,
        [
            ("2026-05-20T13:00:00Z", "2026-05-20T13:30:00Z", "AsleepUnspecified", _SRC, _ING),
            ("2026-05-20T13:30:00Z", "2026-05-20T14:00:00Z", "Awake", _SRC, _ING),
            ("2026-05-20T23:00:00Z", "2026-05-21T04:00:00Z", "AsleepCore", _SRC, _ING),
        ],
    )
    db.recompute_nights(conn, {NIGHT})
    naps, total = conn.execute(
        "SELECT nap_seconds, total_asleep_seconds FROM nightly_summary WHERE night_date = ?",
        [NIGHT],
    ).fetchone()
    assert naps == 1800
    assert total == 18000 + 1800


def test_init_schema_migrates_a_pre_nap_database(conn):
    """An existing DB predates the nap columns; init_schema must add them in place."""
    conn.execute("DROP TABLE nightly_summary")
    conn.execute(
        """CREATE TABLE nightly_summary (
               night_date DATE NOT NULL PRIMARY KEY,
               bed_time TIMESTAMPTZ, wake_time TIMESTAMPTZ,
               time_in_bed_seconds INTEGER, asleep_seconds INTEGER,
               rem_seconds INTEGER, deep_seconds INTEGER, core_seconds INTEGER,
               awake_seconds INTEGER, efficiency_pct DOUBLE, hrv_avg_ms DOUBLE,
               rhr_bpm DOUBLE, computed_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
    )
    db.init_schema(conn)  # must be safe to re-apply, and must add what is missing
    cols = {r[0] for r in conn.execute("DESCRIBE nightly_summary").fetchall()}
    assert {"nap_seconds", "nap_count", "total_asleep_seconds"} <= cols

    _seed(conn)
    db.insert_sleep(conn, NAP_SAMPLES)
    db.recompute_nights(conn, {NIGHT})
    assert (
        conn.execute(
            "SELECT nap_seconds FROM nightly_summary WHERE night_date = ?", [NIGHT]
        ).fetchone()[0]
        == 3600
    )


def test_night_is_the_episode_with_most_sleep(conn):
    """A long restless nap must not be mistaken for the night."""
    _insert_raw(
        conn,
        [
            # Restless afternoon episode: 3 h span, only 1 h asleep.
            ("2026-05-20T12:00:00Z", "2026-05-20T13:00:00Z", "AsleepUnspecified", _SRC, _ING),
            ("2026-05-20T13:00:00Z", "2026-05-20T15:00:00Z", "Awake", _SRC, _ING),
            # Compact night: 2 h span, all asleep.
            ("2026-05-20T23:00:00Z", "2026-05-21T01:00:00Z", "AsleepCore", _SRC, _ING),
        ],
    )
    db.recompute_nights(conn, {NIGHT})
    bed, tib, asleep = conn.execute(
        "SELECT bed_time, time_in_bed_seconds, asleep_seconds FROM nightly_summary "
        "WHERE night_date = ?",
        [NIGHT],
    ).fetchone()
    assert bed.astimezone(UTC) == datetime(2026, 5, 20, 23, 0, tzinfo=UTC)
    assert tib == 7200
    assert asleep == 7200


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
