"""Read-only data layer for the LAN-only monitoring dashboard.

This module never writes — it reads `nightly_summary` and shapes it for a simple
web UI. It is intentionally *declarative*: the `METRICS` registry is the single
source of truth for which parameters the UI shows and how each is labelled,
explained (tooltip) and trended. Adding a new health parameter later means
appending a `Metric` here (and ensuring its value lands in `nightly_summary`) —
the API and the frontend renderer pick it up with no further changes.

Storage is UTC; clock-style fields are localised at read time (CLAUDE.md #4).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

import duckdb

# Localisation for clock fields (bed/wake). Matches db.py / sleep-mcp defaults.
LOCAL_TZ = ZoneInfo(os.environ.get("HEALTHBRIDGE_TZ", "Europe/Amsterdam"))

ALLOWED_FORMATS = {"duration", "percent", "number", "clock"}
ALLOWED_TRENDS = {"higher_better", "lower_better", "neutral"}
# Formats whose stored value is a plain number we can trend on.
_NUMERIC_FORMATS = {"duration", "percent", "number"}

MAX_WINDOW = 90
# A change smaller than this fraction of the baseline counts as "flat".
_FLAT_EPSILON_FRAC = 0.01


@dataclass(frozen=True)
class Metric:
    key: str  # nightly_summary column (or future derived key)
    label: str  # short header text
    group: str  # domain band: "Sleep" | "Recovery" | (future: "Activity", ...)
    unit: str  # "" | "%" | "ms" | "bpm"
    format: str  # one of ALLOWED_FORMATS
    trend: str  # one of ALLOWED_TRENDS
    tooltip: str  # one-sentence plain-language explanation
    # Generic, population-level reference range shown in the tooltip alongside YOUR
    # window average. General adult guidance only — not personalised, not medical advice.
    reference: str = ""


# ── The registry (extensibility seam) ────────────────────────────────────────
# Order here == column order in the UI; metrics are grouped by `group`.
METRICS: list[Metric] = [
    Metric(
        "bed_time",
        "Bed",
        "Sleep",
        "",
        "clock",
        "neutral",
        "Clock time you fell asleep (start of the night), in local time.",
        reference="A consistent bed time matters more than the exact hour.",
    ),
    Metric(
        "wake_time",
        "Wake",
        "Sleep",
        "",
        "clock",
        "neutral",
        "Clock time you woke (end of the night), in local time.",
        reference="A consistent wake time matters more than the exact hour.",
    ),
    Metric(
        "time_in_bed_seconds",
        "In bed",
        "Sleep",
        "",
        "duration",
        "neutral",
        "Total span from first asleep to final wake, including brief awakenings.",
        reference="Adults typically spend ~7–9 h in bed.",
    ),
    Metric(
        "asleep_seconds",
        "Asleep",
        "Sleep",
        "",
        "duration",
        "higher_better",
        "Time actually asleep (Core + Deep + REM). More is generally better.",
        reference="Adults: 7–9 h asleep (National Sleep Foundation).",
    ),
    Metric(
        "efficiency_pct",
        "Efficiency",
        "Sleep",
        "%",
        "percent",
        "higher_better",
        "Asleep time as a percentage of time in bed. Higher means less restless.",
        reference="Healthy sleep efficiency is ~85% or higher.",
    ),
    Metric(
        "deep_seconds",
        "Deep",
        "Sleep",
        "",
        "duration",
        "higher_better",
        "Deep (slow-wave) sleep — the most physically restorative stage.",
        reference="Roughly 13–23% of a night (~1–2 h); declines with age.",
    ),
    Metric(
        "rem_seconds",
        "REM",
        "Sleep",
        "",
        "duration",
        "higher_better",
        "REM sleep — associated with memory and cognitive recovery.",
        reference="Roughly 20–25% of a night (~1.5–2 h).",
    ),
    Metric(
        "core_seconds",
        "Core",
        "Sleep",
        "",
        "duration",
        "neutral",
        "Core (light) sleep — the bulk of the night; neither good nor bad on its own.",
        reference="Light sleep is ~50–60% of a typical night.",
    ),
    Metric(
        "awake_seconds",
        "Awake",
        "Sleep",
        "",
        "duration",
        "lower_better",
        "Time awake during the night after first falling asleep. Less is better.",
        reference="Brief awakenings are normal; under ~20–30 min awake is typical.",
    ),
    Metric(
        "hrv_avg_ms",
        "HRV",
        "Recovery",
        "ms",
        "number",
        "higher_better",
        "Average heart-rate variability (SDNN) over the night. Higher tends to mean "
        "better recovery; only ~2-3 samples per night, so watch the multi-night trend.",
        reference="Overnight SDNN is highly individual and falls with age — very roughly "
        "~20–60 ms for adults. Track your own trend, not the absolute number.",
    ),
    Metric(
        "rhr_bpm",
        "RHR",
        "Recovery",
        "bpm",
        "number",
        "lower_better",
        "Resting heart rate for the day. The arrow compares it to the average of the "
        "other nights in view (your window); a rise above that average can signal strain.",
        reference="Typical adult resting HR is 60–100 bpm; endurance-trained often 40–60. "
        "Lower is generally healthier; rises vs your own norm matter most.",
    ),
]

# nightly_summary columns we read, in query order (night_date drives the rows).
_DB_COLS = ("night_date",) + tuple(m.key for m in METRICS)
_NUMERIC_KEYS = [m.key for m in METRICS if m.format in _NUMERIC_FORMATS]
_CLOCK_KEYS = [m.key for m in METRICS if m.format == "clock"]
_TREND_BY_KEY = {m.key: m.trend for m in METRICS}


def metrics_as_dicts() -> list[dict]:
    """Serialise the registry for the API/frontend (data-driven rendering)."""
    return [asdict(m) for m in METRICS]


def window_averages(nights: list[dict]) -> dict[str, float | None]:
    """Mean of each numeric metric across the nights in view (None if no data).

    This is the "window average" surfaced in the tooltip — the average over ALL
    nights currently shown (distinct from `compute_trends`, which compares the
    latest night to the mean of the *other* nights).
    """
    out: dict[str, float | None] = {}
    for key in _NUMERIC_KEYS:
        vals = [n["values"].get(key) for n in nights if n["values"].get(key) is not None]
        out[key] = mean(vals) if vals else None
    return out


def _clamp_window(window: int) -> int:
    return max(1, min(MAX_WINDOW, window))


def _row_to_night(row: tuple) -> dict:
    d = dict(zip(_DB_COLS, row, strict=True))
    values: dict = {}
    for key in _NUMERIC_KEYS:
        values[key] = d[key]
    for key in _CLOCK_KEYS:
        ts = d[key]
        values[key] = ts.astimezone(LOCAL_TZ).strftime("%H:%M") if ts is not None else None
    return {"night_date": str(d["night_date"]), "values": values}


def fetch_window(conn: duckdb.DuckDBPyConnection, end_date: date | None, window: int) -> dict:
    """Return up to `window` nights at/older than `end_date`, newest first.

    `end_date=None` anchors to the most recent night. Also returns paging
    anchors: `prev_end` (the "Earlier" button target, or None at the start of
    history) and `next_end` (the "Later" target, or None when already showing
    the most recent night). Anchors are computed server-side so the client stays
    dumb.
    """
    window = _clamp_window(window)
    conn.execute("SET TimeZone='UTC'")  # we store UTC; localise clocks ourselves

    latest = conn.execute("SELECT MAX(night_date) FROM nightly_summary").fetchone()[0]
    if latest is None:
        return {
            "window": window,
            "end_date": None,
            "prev_end": None,
            "next_end": None,
            "nights": [],
        }

    effective_end = end_date if end_date is not None else latest

    rows = conn.execute(
        f"SELECT {', '.join(_DB_COLS)} FROM nightly_summary "
        "WHERE night_date <= ? ORDER BY night_date DESC LIMIT ?",
        [effective_end, window],
    ).fetchall()
    nights = [_row_to_night(r) for r in rows]

    prev_end = None
    next_end = None
    if nights:
        oldest_shown = rows[-1][0]  # DATE, descending order -> last is oldest
        older_exists = conn.execute(
            "SELECT 1 FROM nightly_summary WHERE night_date < ? LIMIT 1", [oldest_shown]
        ).fetchone()
        if older_exists:
            prev_end = str(oldest_shown - timedelta(days=1))
        if effective_end < latest:
            next_end = str(min(effective_end + timedelta(days=window), latest))

    return {
        "window": window,
        "end_date": str(effective_end),
        "prev_end": prev_end,
        "next_end": next_end,
        "nights": nights,
    }


def compute_trends(nights: list[dict]) -> dict[str, dict]:
    """Per-metric trend of the latest night vs the mean of the rest of the window.

    Pure function over the `nights` list (newest first) produced by
    `fetch_window`. Returns `{key: {"direction": up|down|flat,
    "sentiment": good|bad|neutral}}` for every numeric metric. Neutral-direction
    metrics always report neutral sentiment; flat (within epsilon), a missing
    latest value, or no baseline all report flat/neutral.
    """
    trends: dict[str, dict] = {}
    if not nights:
        return trends

    latest_vals = nights[0]["values"]
    prior = nights[1:]

    for key in _NUMERIC_KEYS:
        latest = latest_vals.get(key)
        baseline_samples = [n["values"].get(key) for n in prior if n["values"].get(key) is not None]
        if latest is None or not baseline_samples:
            trends[key] = {"direction": "flat", "sentiment": "neutral"}
            continue

        baseline = mean(baseline_samples)
        delta = latest - baseline
        epsilon = max(1e-9, _FLAT_EPSILON_FRAC * abs(baseline))

        if abs(delta) <= epsilon:
            trends[key] = {"direction": "flat", "sentiment": "neutral"}
            continue

        direction = "up" if delta > 0 else "down"
        trend = _TREND_BY_KEY[key]
        if trend == "neutral":
            sentiment = "neutral"
        elif trend == "higher_better":
            sentiment = "good" if direction == "up" else "bad"
        else:  # lower_better
            sentiment = "bad" if direction == "up" else "good"
        trends[key] = {"direction": direction, "sentiment": sentiment}

    return trends
