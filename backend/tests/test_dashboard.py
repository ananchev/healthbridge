"""Tests for the LAN-only monitoring dashboard (read-only).

Covers the metric registry, the windowed fetch (paging anchors), the pure
trend computation, and that the HTTP surface is unauthenticated (LAN-only)
while the existing /ingest auth stays intact.
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import healthbridge.app as app_module
import healthbridge.auth as auth_module
from healthbridge import dashboard, db
from healthbridge.app import app

UTC = ZoneInfo("UTC")


# --- helpers ---------------------------------------------------------------


def _insert_night(
    conn,
    night: date,
    *,
    asleep: int | None = 25000,
    efficiency: float | None = 92.0,
    deep: int | None = 2000,
    rem: int | None = 6000,
    core: int | None = 15000,
    awake: int | None = 1500,
    time_in_bed: int | None = 27000,
    hrv: float | None = 35.0,
    rhr: float | None = 58.0,
    bed_hour_utc: int = 20,
    wake_hour_utc: int = 4,
) -> None:
    """Insert one nightly_summary row directly (isolates dashboard from recompute)."""
    bed = datetime(night.year, night.month, night.day, bed_hour_utc, 30, tzinfo=UTC)
    wake = datetime(night.year, night.month, night.day, wake_hour_utc, 0, tzinfo=UTC)
    conn.execute(
        """INSERT INTO nightly_summary (
            night_date, bed_time, wake_time, time_in_bed_seconds,
            asleep_seconds, rem_seconds, deep_seconds, core_seconds, awake_seconds,
            efficiency_pct, hrv_avg_ms, rhr_bpm
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [night, bed, wake, time_in_bed, asleep, rem, deep, core, awake, efficiency, hrv, rhr],
    )


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        c = db.connect(str(Path(tmp) / "test.duckdb"))
        db.init_schema(c)
        yield c
        c.close()


# --- registry integrity ----------------------------------------------------


def test_registry_keys_and_enums():
    keys = [m.key for m in dashboard.METRICS]
    assert len(keys) == len(set(keys)), "metric keys must be unique"
    for m in dashboard.METRICS:
        assert m.format in dashboard.ALLOWED_FORMATS
        assert m.trend in dashboard.ALLOWED_TRENDS
        assert m.group and m.label and m.tooltip


def test_registry_numeric_columns_exist_in_schema(conn):
    """Every non-derived metric key must be a real nightly_summary column."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info('nightly_summary')").fetchall()}
    for m in dashboard.METRICS:
        assert m.key in cols, f"{m.key} is not a nightly_summary column"


def test_metrics_as_dicts_shape():
    dicts = dashboard.metrics_as_dicts()
    assert dicts[0].keys() >= {"key", "label", "group", "unit", "format", "trend", "tooltip"}


# --- fetch_window ----------------------------------------------------------


def _seed_range(conn, start: date, n: int) -> list[date]:
    from datetime import timedelta

    nights = [start + timedelta(days=i) for i in range(n)]
    for nd in nights:
        _insert_night(conn, nd)
    return nights


def test_fetch_window_default_end_is_latest(conn):
    nights = _seed_range(conn, date(2026, 5, 28), 10)  # ends 2026-06-06
    res = dashboard.fetch_window(conn, None, 7)
    assert res["end_date"] == str(nights[-1])
    assert len(res["nights"]) == 7
    # descending: most recent first
    returned = [n["night_date"] for n in res["nights"]]
    assert returned == sorted(returned, reverse=True)
    assert returned[0] == str(nights[-1])


def test_fetch_window_respects_window_size(conn):
    _seed_range(conn, date(2026, 5, 1), 20)
    assert len(dashboard.fetch_window(conn, None, 14)["nights"]) == 14


def test_fetch_window_paging_anchors(conn):
    nights = _seed_range(conn, date(2026, 5, 28), 10)  # 2026-05-28 .. 2026-06-06
    res = dashboard.fetch_window(conn, None, 7)
    # latest window: oldest shown = 2026-05-31; earlier data exists -> prev_end set
    assert res["prev_end"] == "2026-05-30"
    # already at most recent -> no "Later"
    assert res["next_end"] is None

    # page earlier
    res2 = dashboard.fetch_window(conn, date(2026, 5, 30), 7)
    returned = [n["night_date"] for n in res2["nights"]]
    assert returned[0] == "2026-05-30"
    # only 3 nights older than/equal 05-30 exist (05-28, 05-29, 05-30)
    assert len(returned) == 3
    assert res2["prev_end"] is None  # no data before 2026-05-28
    assert res2["next_end"] is not None  # newer data exists -> can go Later
    assert nights  # sanity


def test_fetch_window_empty_db(conn):
    res = dashboard.fetch_window(conn, None, 7)
    assert res["nights"] == []
    assert res["prev_end"] is None
    assert res["next_end"] is None


def test_fetch_window_clamps_window(conn):
    _seed_range(conn, date(2026, 1, 1), 5)
    assert dashboard.fetch_window(conn, None, 0)["window"] == 1
    assert dashboard.fetch_window(conn, None, 9999)["window"] == dashboard.MAX_WINDOW


def test_fetch_window_localizes_clock(conn):
    # bed at 20:30 UTC on 2026-06-05 -> Amsterdam CEST (UTC+2) = 22:30
    _insert_night(conn, date(2026, 6, 6), bed_hour_utc=20, wake_hour_utc=4)
    res = dashboard.fetch_window(conn, None, 1)
    assert res["nights"][0]["values"]["bed_time"] == "22:30"


# --- compute_trends (pure) -------------------------------------------------


def _nights(latest_vals: dict, prior_vals: dict, n_prior: int = 3) -> list[dict]:
    """Build a descending nights list: latest first, then n_prior identical priors."""
    rows = [{"night_date": "2026-06-06", "values": latest_vals}]
    for i in range(n_prior):
        rows.append({"night_date": f"2026-06-0{5 - i}", "values": dict(prior_vals)})
    return rows


def test_trend_higher_better_up_is_good():
    rows = _nights({"asleep_seconds": 30000}, {"asleep_seconds": 25000})
    t = dashboard.compute_trends(rows)["asleep_seconds"]
    assert t == {"direction": "up", "sentiment": "good"}


def test_trend_higher_better_down_is_bad():
    rows = _nights({"asleep_seconds": 20000}, {"asleep_seconds": 25000})
    t = dashboard.compute_trends(rows)["asleep_seconds"]
    assert t == {"direction": "down", "sentiment": "bad"}


def test_trend_lower_better_inverts():
    rows = _nights({"rhr_bpm": 64.0}, {"rhr_bpm": 58.0})
    up = dashboard.compute_trends(rows)["rhr_bpm"]
    assert up == {"direction": "up", "sentiment": "bad"}

    rows2 = _nights({"rhr_bpm": 52.0}, {"rhr_bpm": 58.0})
    down = dashboard.compute_trends(rows2)["rhr_bpm"]
    assert down == {"direction": "down", "sentiment": "good"}


def test_trend_neutral_metric_has_neutral_sentiment():
    rows = _nights({"core_seconds": 20000}, {"core_seconds": 15000})
    t = dashboard.compute_trends(rows)["core_seconds"]
    assert t["sentiment"] == "neutral"


def test_trend_flat_within_epsilon():
    rows = _nights({"asleep_seconds": 25010}, {"asleep_seconds": 25000})
    t = dashboard.compute_trends(rows)["asleep_seconds"]
    assert t == {"direction": "flat", "sentiment": "neutral"}


def test_trend_single_night_all_flat():
    rows = [{"night_date": "2026-06-06", "values": {"asleep_seconds": 25000, "rhr_bpm": 58.0}}]
    trends = dashboard.compute_trends(rows)
    assert trends["asleep_seconds"]["direction"] == "flat"
    assert trends["rhr_bpm"]["direction"] == "flat"


def test_trend_missing_latest_value_is_flat():
    rows = _nights({"hrv_avg_ms": None}, {"hrv_avg_ms": 35.0})
    t = dashboard.compute_trends(rows)["hrv_avg_ms"]
    assert t == {"direction": "flat", "sentiment": "neutral"}


# --- window_averages (pure) ------------------------------------------------


def test_window_averages_means_over_all_visible_nights():
    rows = [
        {"night_date": "2026-06-06", "values": {"asleep_seconds": 30000, "rhr_bpm": 60.0}},
        {"night_date": "2026-06-05", "values": {"asleep_seconds": 20000, "rhr_bpm": None}},
    ]
    avg = dashboard.window_averages(rows)
    assert avg["asleep_seconds"] == 25000  # mean of all
    assert avg["rhr_bpm"] == 60.0  # None skipped
    assert avg["hrv_avg_ms"] is None  # absent everywhere -> None


def test_window_averages_only_numeric_keys():
    avg = dashboard.window_averages([])
    assert set(avg) == {m.key for m in dashboard.METRICS if m.format != "clock"}


def test_reference_present_for_every_metric():
    """Each parameter carries a generic reference string for the tooltip."""
    assert all(m.reference for m in dashboard.METRICS)


# --- HTTP surface (LAN-only, no auth) --------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setattr(auth_module, "DEV_MODE", False)
    monkeypatch.setattr(auth_module, "EXPECTED_TOKEN", "testtoken")
    with TestClient(app) as c:
        yield c


def test_dashboard_page_open_no_auth(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_data_open_no_auth(client):
    resp = client.get("/dashboard/data")
    assert resp.status_code == 200
    body = resp.json()
    assert "metrics" in body and "nights" in body and "trends" in body


def test_dashboard_data_window_param(client):
    resp = client.get("/dashboard/data", params={"window": 14})
    assert resp.status_code == 200
    assert resp.json()["window"] == 14


def test_ingest_still_requires_auth(client):
    """Adding an open dashboard must NOT loosen the write/auth surface."""
    payload = {
        "device_id": "test-device",
        "synced_at": "2026-05-20T22:14:00Z",
        "data": {"sleep": [], "hrv": [], "rhr": []},
    }
    resp = client.post("/ingest", json=payload)  # no Authorization header
    assert resp.status_code == 401
