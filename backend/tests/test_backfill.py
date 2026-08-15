"""Tests for the one-shot summary backfill (healthbridge.backfill).

The nightly summary is derived data: when the way it is computed changes, every
existing row is stale until it is recomputed. This module rebuilds them all from
the raw samples, which stay untouched.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from healthbridge import backfill, db
from healthbridge.models import SleepSample

_SRC = "Apple Watch test"

# Two nights, each a simple contiguous block.
NIGHT_1 = date(2026, 5, 21)
NIGHT_2 = date(2026, 5, 22)
SAMPLES = [
    SleepSample(
        start="2026-05-20T22:00:00Z", end="2026-05-21T04:00:00Z", stage="AsleepCore", source=_SRC
    ),
    SleepSample(
        start="2026-05-21T23:00:00Z", end="2026-05-22T05:00:00Z", stage="AsleepCore", source=_SRC
    ),
]


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(str(Path(tmp) / "test.duckdb"))
        db.init_schema(c)
        yield c
        c.close()


def test_all_nights_covers_every_sample(conn):
    db.insert_sleep(conn, SAMPLES)
    assert backfill.all_nights(conn) == {NIGHT_1, NIGHT_2}


def test_all_nights_empty_database(conn):
    assert backfill.all_nights(conn) == set()


def test_backfill_recomputes_every_night(conn):
    db.insert_sleep(conn, SAMPLES)
    assert conn.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0] == 0

    recomputed = backfill.backfill(conn)

    assert recomputed == [NIGHT_1, NIGHT_2]
    assert conn.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0] == 2


def test_backfill_repairs_a_stale_row(conn):
    """The case this exists for: a row computed by the old, double-counting logic."""
    db.insert_sleep(conn, SAMPLES)
    conn.execute(
        """INSERT INTO nightly_summary
           (night_date, time_in_bed_seconds, asleep_seconds, efficiency_pct)
           VALUES (?, ?, ?, ?)""",
        [NIGHT_1, 21600, 43200, 200.0],  # 12 h "asleep" inside a 6 h night
    )

    backfill.backfill(conn)

    tib, asleep, eff, total = conn.execute(
        """SELECT time_in_bed_seconds, asleep_seconds, efficiency_pct, total_asleep_seconds
           FROM nightly_summary WHERE night_date = ?""",
        [NIGHT_1],
    ).fetchone()
    assert tib == 21600
    assert asleep == 21600
    assert eff == pytest.approx(100.0)
    assert total == 21600


def test_backfill_is_idempotent(conn):
    db.insert_sleep(conn, SAMPLES)
    backfill.backfill(conn)
    first = conn.execute("SELECT * FROM nightly_summary ORDER BY night_date").fetchall()

    backfill.backfill(conn)
    second = conn.execute("SELECT * FROM nightly_summary ORDER BY night_date").fetchall()

    assert conn.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0] == 2
    # computed_at moves; every derived value must not.
    assert [r[:-1] for r in first] == [r[:-1] for r in second]


def test_backfill_leaves_raw_samples_untouched(conn):
    db.insert_sleep(conn, SAMPLES)
    before = conn.execute("SELECT * FROM sleep_samples ORDER BY start_ts").fetchall()
    backfill.backfill(conn)
    assert conn.execute("SELECT * FROM sleep_samples ORDER BY start_ts").fetchall() == before


def test_main_runs_against_a_db_path(tmp_path, capsys):
    path = str(tmp_path / "main.duckdb")
    conn = db.connect(path)
    db.init_schema(conn)
    db.insert_sleep(conn, SAMPLES)
    conn.close()

    assert backfill.main(["--db", path]) == 0
    assert "2" in capsys.readouterr().out  # reports how many nights it rebuilt

    conn = db.connect(path, read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0] == 2
    conn.close()
