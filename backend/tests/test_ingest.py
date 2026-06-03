"""Tests for the ingestion service.

The MOST IMPORTANT test (per CLAUDE.md): ingest is idempotent. Sending the same
batch twice must NOT create duplicate rows.

Night-boundary and efficiency-math tests live in test_night_logic.py and
test_summary.py respectively.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from healthbridge import db
from healthbridge.models import SleepSample


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(str(Path(tmp) / "test.duckdb"))
        db.init_schema(c)
        yield c
        c.close()


def test_ingest_is_idempotent(conn):
    """Inserting the same sleep batch twice yields no duplicates."""
    samples = [
        SleepSample(
            start="2026-05-20T20:00:00Z",
            end="2026-05-21T04:00:00Z",
            stage="InBed",
            source="Apple Watch test",
        )
    ]
    assert db.insert_sleep(conn, samples) == 1
    assert db.insert_sleep(conn, samples) == 0
    assert conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0] == 1
