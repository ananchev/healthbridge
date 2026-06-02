"""Tests for the ingestion service.

The MOST IMPORTANT test (per CLAUDE.md): ingest is idempotent. Sending the same
batch twice must NOT create duplicate rows.

NOTE for Claude Code: implement these against an in-memory or temp-file DuckDB.
Add more: night-boundary correctness, efficiency math, HRV averaging.
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
    """Inserting the same sleep batch twice yields no duplicates.

    TODO(claude-code): build a small list[SleepSample], insert_sleep twice,
    assert the second call returns 0 new rows and the table row count is stable.
    """
    pytest.skip("implement once db.insert_sleep is written")


def test_night_assignment_noon_boundary():
    """A 23:30 start belongs to the next morning's night; 06:00 to that date.

    TODO(claude-code): assert db.assign_to_night behaviour around the noon split,
    including a sample exactly at 12:00 (-> next day).
    """
    pytest.skip("implement")


def test_efficiency_math(conn):
    """efficiency_pct = asleep / time_in_bed * 100, within tolerance.

    TODO(claude-code): insert a synthetic night, recompute, assert the row.
    """
    pytest.skip("implement")
