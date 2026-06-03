"""Tests for assign_to_night: noon-to-noon boundary, DST, UTC input."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from healthbridge.db import assign_to_night

UTC = ZoneInfo("UTC")


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_assign_before_noon_local():
    """06:00 Amsterdam CEST = 04:00 UTC → hour 6 < 12 → that date."""
    assert assign_to_night(_utc("2026-05-21T04:00:00+00:00")) == date(2026, 5, 21)


def test_assign_at_noon_exact():
    """Exactly 12:00 Amsterdam CEST = 10:00 UTC → hour 12 >= 12 → next date."""
    assert assign_to_night(_utc("2026-05-21T10:00:00+00:00")) == date(2026, 5, 22)


def test_assign_just_before_noon():
    """11:59 Amsterdam CEST = 09:59 UTC → hour 11 < 12 → that date."""
    assert assign_to_night(_utc("2026-05-21T09:59:00+00:00")) == date(2026, 5, 21)


def test_assign_after_noon_local():
    """23:30 Amsterdam CEST on May 20 = 21:30 UTC → hour 23 >= 12 → night May 21."""
    assert assign_to_night(_utc("2026-05-20T21:30:00+00:00")) == date(2026, 5, 21)


def test_assign_dst_spring_forward_evening():
    """23:00 CET on March 28 (before DST) = 22:00 UTC → hour 23 >= 12 → night March 29."""
    assert assign_to_night(_utc("2026-03-28T22:00:00+00:00")) == date(2026, 3, 29)


def test_assign_dst_spring_forward_morning():
    """07:00 CEST on March 29 (after DST spring-forward) = 05:00 UTC → night March 29."""
    assert assign_to_night(_utc("2026-03-29T05:00:00+00:00")) == date(2026, 3, 29)


def test_assign_utc_input():
    """UTC-aware input is correctly shifted to local before boundary check."""
    # midnight UTC May 21 = 02:00 CEST May 21 → hour 2 < 12 → night May 21
    assert assign_to_night(_utc("2026-05-21T00:00:00+00:00")) == date(2026, 5, 21)
