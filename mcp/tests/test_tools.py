"""Contract tests for sleep-mcp tools (read-only).

Per docs/TESTING.md rule 7: seed a temp DuckDB (real schema), call each tool
function directly, assert shape + values. get_recovery_status covers green/yellow/red.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb

from sleep_mcp import server

SCHEMA = Path(__file__).resolve().parents[2] / "backend" / "healthbridge" / "schema.sql"


def _make_db(tmp_path, nights):
    """Create a DuckDB at tmp_path with the real schema, insert `nights`.

    Each night is a dict; missing keys default to None. `night_date` required.
    """
    db = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(SCHEMA.read_text())
    for n in nights:
        conn.execute(
            """INSERT INTO nightly_summary
               (night_date, bed_time, wake_time, time_in_bed_seconds, asleep_seconds,
                rem_seconds, deep_seconds, core_seconds, awake_seconds,
                efficiency_pct, hrv_avg_ms, rhr_bpm,
                nap_seconds, nap_count, total_asleep_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                n["night_date"],
                n.get("bed_time"),
                n.get("wake_time"),
                n.get("time_in_bed_seconds"),
                n.get("asleep_seconds"),
                n.get("rem_seconds"),
                n.get("deep_seconds"),
                n.get("core_seconds"),
                n.get("awake_seconds"),
                n.get("efficiency_pct"),
                n.get("hrv_avg_ms"),
                n.get("rhr_bpm"),
                # Left NULL by default: rows written before the nap columns
                # existed look exactly like this until the backfill runs.
                n.get("nap_seconds"),
                n.get("nap_count"),
                n.get("total_asleep_seconds"),
            ],
        )
    conn.close()
    return str(db)


def _seed(monkeypatch, tmp_path, nights):
    monkeypatch.setattr(server, "DB_PATH", _make_db(tmp_path, nights))


def _baseline_nights(n, latest_day, *, hrv=50.0, rhr=55.0, asleep_h=8.0):
    """n healthy nights ending the day before latest_day (the baseline history)."""
    return [
        {
            "night_date": latest_day - timedelta(days=i + 1),
            "asleep_seconds": int(asleep_h * 3600),
            "hrv_avg_ms": hrv,
            "rhr_bpm": rhr,
        }
        for i in range(n)
    ]


# ── basic reads ───────────────────────────────────────────────────────────────


def test_get_latest_night(monkeypatch, tmp_path):
    _seed(
        monkeypatch,
        tmp_path,
        [
            {"night_date": date(2026, 6, 1), "asleep_seconds": 1, "hrv_avg_ms": 1},
            {
                "night_date": date(2026, 6, 2),
                # TIMESTAMPTZ columns (stored UTC) — exercises the DuckDB→datetime
                # (pytz) read path AND the read-time localization to Europe/Amsterdam.
                "bed_time": "2026-06-01T20:00:00+00:00",  # → 22:00 CEST
                "wake_time": "2026-06-02T03:05:00+00:00",  # → 05:05 CEST
                "time_in_bed_seconds": 25500,  # 7h05m
                "asleep_seconds": 24420,  # 6h47m
                "efficiency_pct": 95.76,
                "hrv_avg_ms": 41.23,
                "rhr_bpm": 55.0,
            },
        ],
    )
    out = server.get_latest_night()
    assert out["night_date"] == "2026-06-02"
    assert out["asleep"] == "6h47m"
    assert out["time_in_bed"] == "7h05m"
    assert out["asleep_seconds"] == 24420
    assert out["efficiency_pct"] == 95.8  # rounded to 1dp
    assert out["hrv_avg_ms"] == 41.2
    # TIMESTAMPTZ localized to Europe/Amsterdam (DST-aware: June → CEST +02:00).
    # Regression guard for both the pytz import path and the tz conversion applying.
    assert out["bed_time"] == "2026-06-01T22:00:00+02:00"
    assert out["wake_time"] == "2026-06-02T05:05:00+02:00"


def test_get_latest_night_empty(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [])
    assert server.get_latest_night() == {}


def test_get_nightly_range(monkeypatch, tmp_path):
    nights = [{"night_date": date(2026, 6, d), "asleep_seconds": 28800} for d in (1, 2, 3, 4)]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_nightly_range(date(2026, 6, 2), date(2026, 6, 3))
    assert [r["night_date"] for r in out] == ["2026-06-02", "2026-06-03"]


def test_get_nightly_summary_absent(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [{"night_date": date(2026, 6, 1), "asleep_seconds": 1}])
    assert server.get_nightly_summary(date(2020, 1, 1)) == {}


# ── recovery status ───────────────────────────────────────────────────────────


def test_recovery_status_green(monkeypatch, tmp_path):
    latest = date(2026, 6, 15)
    nights = _baseline_nights(10, latest) + [
        {"night_date": latest, "asleep_seconds": 28800, "hrv_avg_ms": 50.0, "rhr_bpm": 55.0}
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_recovery_status()
    assert out["status"] == "green"
    assert out["reasons"] == []
    # Baseline is labelled + excludes the latest night (10 prior nights here).
    assert out["metrics"]["baseline_excludes_latest"] is True
    assert out["metrics"]["hrv_baseline_nights"] == 10
    assert out["metrics"]["rhr_baseline_nights"] == 10


def test_recovery_status_yellow(monkeypatch, tmp_path):
    latest = date(2026, 6, 15)
    # One flag: HRV depressed (30 vs 50 baseline → below 0.8×). RHR + sleep normal.
    nights = _baseline_nights(10, latest) + [
        {"night_date": latest, "asleep_seconds": 28800, "hrv_avg_ms": 30.0, "rhr_bpm": 55.0}
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_recovery_status()
    assert out["status"] == "yellow"
    assert len(out["reasons"]) == 1


def test_recovery_status_red(monkeypatch, tmp_path):
    latest = date(2026, 6, 15)
    # Two flags: HRV depressed AND RHR elevated (65 vs 55 baseline → >+5).
    nights = _baseline_nights(10, latest) + [
        {"night_date": latest, "asleep_seconds": 28800, "hrv_avg_ms": 30.0, "rhr_bpm": 65.0}
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_recovery_status()
    assert out["status"] == "red"
    assert len(out["reasons"]) >= 2


def test_recovery_status_no_data(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [])
    assert server.get_recovery_status()["status"] == "unknown"


# ── trends ────────────────────────────────────────────────────────────────────


def test_get_sleep_debt_cumulative_vs_net(monkeypatch, tmp_path):
    # 6h, 6h, 10h vs 8h target. Cumulative shortfall floors oversleep at 0:
    #   debt_hours = 2 + 2 + 0 = 4.0
    # Net credits the 10h night: net = 8*3 - 22 = 2.0
    nights = [
        {"night_date": date(2026, 6, 1), "asleep_seconds": 6 * 3600},
        {"night_date": date(2026, 6, 2), "asleep_seconds": 6 * 3600},
        {"night_date": date(2026, 6, 3), "asleep_seconds": 10 * 3600},
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_sleep_debt(window_days=7, target_hours=8.0)
    assert out["debt_hours"] == 4.0
    assert out["net_debt_hours"] == 2.0
    assert out["nights_counted"] == 3


def test_night_reports_nap_fields(monkeypatch, tmp_path):
    """Naps ride alongside the night, never folded into it."""
    _seed(
        monkeypatch,
        tmp_path,
        [
            {
                "night_date": date(2026, 6, 2),
                "asleep_seconds": 6 * 3600,
                "nap_seconds": 3600,
                "nap_count": 1,
                "total_asleep_seconds": 7 * 3600,
            }
        ],
    )
    out = server.get_latest_night()
    assert out["asleep_seconds"] == 6 * 3600  # the night alone
    assert out["nap_seconds"] == 3600
    assert out["nap_count"] == 1
    assert out["total_asleep_seconds"] == 7 * 3600
    assert out["total_asleep"] == "7h00m"


def test_sleep_debt_counts_nap_sleep(monkeypatch, tmp_path):
    """A 6 h night plus a 1 h nap is 7 h of sleep, not 6."""
    nights = [
        {
            "night_date": date(2026, 6, 1),
            "asleep_seconds": 6 * 3600,
            "nap_seconds": 3600,
            "nap_count": 1,
            "total_asleep_seconds": 7 * 3600,
        },
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_sleep_debt(window_days=7, target_hours=8.0)
    assert out["debt_hours"] == 1.0
    assert out["per_night"][0]["asleep_hours"] == 7.0
    assert out["per_night"][0]["nap_hours"] == 1.0


def test_sleep_debt_falls_back_when_total_missing(monkeypatch, tmp_path):
    """Rows written before the backfill have NULL totals — read the night, not 0.

    Treating NULL as zero would invent a full night of debt per un-backfilled row.
    """
    nights = [{"night_date": date(2026, 6, 1), "asleep_seconds": 6 * 3600}]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_sleep_debt(window_days=7, target_hours=8.0)
    assert out["debt_hours"] == 2.0
    assert out["per_night"][0]["asleep_hours"] == 6.0


def test_recovery_status_counts_nap_sleep(monkeypatch, tmp_path):
    """Sleep debt inside the recovery signal uses the same 24 h total."""
    latest = date(2026, 6, 30)
    nights = _baseline_nights(14, latest, asleep_h=4.0) + [
        {
            "night_date": latest,
            "asleep_seconds": 4 * 3600,
            "nap_seconds": 4 * 3600,
            "nap_count": 1,
            "total_asleep_seconds": 8 * 3600,
            "hrv_avg_ms": 50.0,
            "rhr_bpm": 55.0,
        }
    ]
    naps_dir = tmp_path / "naps"
    naps_dir.mkdir()
    monkeypatch.setattr(server, "DB_PATH", _make_db(naps_dir, nights))
    debt_with_naps = server.get_recovery_status()["metrics"]["sleep_debt_hours"]

    nights_no_naps = [
        {**n, "nap_seconds": 0, "total_asleep_seconds": n["asleep_seconds"]} for n in nights
    ]
    plain_dir = tmp_path / "nonaps"
    plain_dir.mkdir()
    monkeypatch.setattr(server, "DB_PATH", _make_db(plain_dir, nights_no_naps))
    debt_without = server.get_recovery_status()["metrics"]["sleep_debt_hours"]

    assert debt_with_naps < debt_without


def test_get_hrv_trend(monkeypatch, tmp_path):
    nights = [
        {"night_date": date(2026, 6, 1), "hrv_avg_ms": 40.0},
        {"night_date": date(2026, 6, 2), "hrv_avg_ms": 60.0},
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_hrv_trend(days=30)
    assert out["baseline_ms"] == 50.0
    assert out["baseline_nights"] == 2
    assert out["baseline_includes_latest"] is True
    assert [p["night_date"] for p in out["series"]] == ["2026-06-01", "2026-06-02"]


def test_discovery_aliases():
    from starlette.testclient import TestClient

    with TestClient(server.build_app()) as c:
        assert c.get("/").status_code == 200
        assert c.get("/").json() == {"status": "ok"}
        r = c.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200
        assert r.json()["resource"].endswith("/mcp")
        assert r.json()["authorization_servers"]
