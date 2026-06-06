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
                efficiency_pct, hrv_avg_ms, rhr_bpm)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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


def test_get_sleep_debt(monkeypatch, tmp_path):
    # 3 nights at 6h vs 8h target → 6h debt.
    nights = [{"night_date": date(2026, 6, d), "asleep_seconds": 6 * 3600} for d in (1, 2, 3)]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_sleep_debt(window_days=7, target_hours=8.0)
    assert out["debt_hours"] == 6.0
    assert out["nights_counted"] == 3


def test_get_hrv_trend(monkeypatch, tmp_path):
    nights = [
        {"night_date": date(2026, 6, 1), "hrv_avg_ms": 40.0},
        {"night_date": date(2026, 6, 2), "hrv_avg_ms": 60.0},
    ]
    _seed(monkeypatch, tmp_path, nights)
    out = server.get_hrv_trend(days=30)
    assert out["baseline_ms"] == 50.0
    assert [p["night_date"] for p in out["series"]] == ["2026-06-01", "2026-06-02"]
