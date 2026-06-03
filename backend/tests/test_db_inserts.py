"""Tests for idempotent DB inserts (db.py).

Each type: correct new-row count on first insert, zero on re-insert,
partial overlap, and empty list.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from healthbridge import db
from healthbridge.models import HrvSample, RhrSample, SleepSample


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(str(Path(tmp) / "test.duckdb"))
        db.init_schema(c)
        yield c
        c.close()


def _sleep(start: str, end: str, stage: str = "AsleepCore") -> SleepSample:
    return SleepSample(
        start=start,
        end=end,
        stage=stage,
        source="Apple Watch test",
    )


def _hrv(ts: str, value_ms: float = 34.0) -> HrvSample:
    return HrvSample(timestamp=ts, value_ms=value_ms, source="Apple Watch test")


def _rhr(d: str, value_bpm: float = 55.0) -> RhrSample:
    return RhrSample(date=date.fromisoformat(d), value_bpm=value_bpm, source="Apple Watch test")


# --- sleep ---


def test_insert_sleep_returns_new_count(conn):
    samples = [
        _sleep("2026-05-20T20:00:00Z", "2026-05-21T04:00:00Z", "InBed"),
        _sleep("2026-05-20T20:30:00Z", "2026-05-20T21:30:00Z", "AsleepCore"),
    ]
    assert db.insert_sleep(conn, samples) == 2


def test_insert_sleep_idempotent(conn):
    samples = [
        _sleep("2026-05-20T20:00:00Z", "2026-05-21T04:00:00Z", "InBed"),
        _sleep("2026-05-20T20:30:00Z", "2026-05-20T21:30:00Z", "AsleepCore"),
    ]
    db.insert_sleep(conn, samples)
    assert db.insert_sleep(conn, samples) == 0
    assert conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0] == 2


def test_insert_sleep_partial_overlap(conn):
    first = [_sleep("2026-05-20T20:00:00Z", "2026-05-21T04:00:00Z", "InBed")]
    second = [
        _sleep("2026-05-20T20:00:00Z", "2026-05-21T04:00:00Z", "InBed"),  # duplicate
        _sleep("2026-05-20T20:30:00Z", "2026-05-20T21:30:00Z", "AsleepCore"),  # new
    ]
    db.insert_sleep(conn, first)
    assert db.insert_sleep(conn, second) == 1
    assert conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0] == 2


def test_insert_sleep_empty(conn):
    assert db.insert_sleep(conn, []) == 0


# --- hrv ---


def test_insert_hrv_returns_new_count(conn):
    samples = [_hrv("2026-05-20T21:00:00Z", 34.2), _hrv("2026-05-21T01:00:00Z", 38.6)]
    assert db.insert_hrv(conn, samples) == 2


def test_insert_hrv_idempotent(conn):
    samples = [_hrv("2026-05-20T21:00:00Z", 34.2), _hrv("2026-05-21T01:00:00Z", 38.6)]
    db.insert_hrv(conn, samples)
    assert db.insert_hrv(conn, samples) == 0
    assert conn.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0] == 2


def test_insert_hrv_empty(conn):
    assert db.insert_hrv(conn, []) == 0


# --- rhr ---


def test_insert_rhr_returns_new_count(conn):
    samples = [_rhr("2026-05-21"), _rhr("2026-05-22")]
    assert db.insert_rhr(conn, samples) == 2


def test_insert_rhr_idempotent(conn):
    samples = [_rhr("2026-05-21"), _rhr("2026-05-22")]
    db.insert_rhr(conn, samples)
    assert db.insert_rhr(conn, samples) == 0
    assert conn.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0] == 2


def test_insert_rhr_empty(conn):
    assert db.insert_rhr(conn, []) == 0
