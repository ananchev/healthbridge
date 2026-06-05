"""Adapter: Health Auto Export (HAE) JSON → canonical IngestData.

HAE (https://github.com/Lybron/health-auto-export) POSTs a different wire shape
than our native /ingest contract. This module normalizes it to the SAME
`IngestData` models so the existing db.py write path is reused verbatim — single
writer, idempotency, and recompute all unchanged. See docs/HAE_SETUP.md.

Expected HAE settings (see docs/HAE_SETUP.md): summarize OFF, time grouping
minutes, metrics = Sleep + Heart Rate Variability + Resting Heart Rate only.

HAE shape (relevant subset):
    {"data": {"metrics": [
        {"name": "sleep_analysis", "units": "hr",
         "data": [{"start": "...", "end": "...", "value": "Core",
                   "source": "Apple Watch van Owner"}]},
        {"name": "heart_rate_variability", "units": "ms",
         "data": [{"date": "...", "qty": 31.5, "source": "..."}]},
        {"name": "resting_heart_rate", "units": "count/min",
         "data": [{"date": "...", "qty": 57, "source": "..."}]}
    ]}}

Key correctness rules (learned from real exports):
  - Both Apple Watch AND SleepWatch write sleep; HealthKit's per-type export
    carries both. We MUST filter to the configured Apple Watch source. The source
    name contains a NON-BREAKING SPACE (U+00A0); matching normalizes NBSP→space so
    the configured value can use a regular space, but the ORIGINAL string is stored.
  - HAE timestamps are local wall-clock with offset ("2026-05-06 01:09:08 +0200").
    Sleep/HRV are stored as UTC TIMESTAMPTZ → convert. RHR is keyed by calendar
    DATE: its `date` is local midnight (e.g. "00:00:38 +0200"); converting to UTC
    first would shift it to the previous day, so RHR takes the LOCAL date as-is.
  - HAE stage strings are short (Core/Deep/REM/Asleep/Awake) — map to our canonical
    AsleepCore/AsleepDeep/AsleepREM/AsleepUnspecified/Awake/InBed.
  - Unknown metrics and unmapped stages are ignored defensively (an all-on export
    must not crash ingest).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, Field

from .models import HrvSample, IngestData, RhrSample, SleepSample

# HAE short stage value → canonical backend stage (HKCategoryValueSleepAnalysis,
# prefix stripped). "Asleep" is Apple's generic/unspecified asleep state.
STAGE_MAP: dict[str, str] = {
    "Core": "AsleepCore",
    "Deep": "AsleepDeep",
    "REM": "AsleepREM",
    "Asleep": "AsleepUnspecified",
    "Unspecified": "AsleepUnspecified",
    "Awake": "Awake",
    "In Bed": "InBed",
    "InBed": "InBed",
}

_HAE_DT = "%Y-%m-%d %H:%M:%S %z"


# --- HAE wire models (thin; per-metric rows stay as dicts since shape varies) ---


class HAEMetric(BaseModel):
    name: str
    units: str | None = None
    data: list[dict] = Field(default_factory=list)


class HAEData(BaseModel):
    metrics: list[HAEMetric] = Field(default_factory=list)


class HAEPayload(BaseModel):
    data: HAEData


# --- helpers ------------------------------------------------------------------


def _norm_source(s: str | None) -> str:
    """Normalize for comparison: NBSP→space, trimmed. Used for matching only."""
    return (s or "").replace(" ", " ").strip()


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, _HAE_DT)


def _parse_utc(s: str) -> datetime:
    return _parse_dt(s).astimezone(UTC)


def _parse_local_date(s: str) -> date:
    # Take the local calendar date BEFORE any UTC conversion (RHR is keyed by day).
    return _parse_dt(s).date()


def _matches(source: str | None, sleep_source: str | None) -> bool:
    if sleep_source is None:
        return True
    return _norm_source(source) == _norm_source(sleep_source)


# --- normalization ------------------------------------------------------------


def normalize(payload: HAEPayload, *, sleep_source: str | None = None) -> IngestData:
    """Convert an HAE payload to canonical IngestData.

    If `sleep_source` is set, only rows from that source are kept (NBSP-insensitive
    match); the original source string is stored verbatim. Unknown metrics and
    unmapped sleep stages are skipped.
    """
    sleep: list[SleepSample] = []
    hrv: list[HrvSample] = []
    rhr: list[RhrSample] = []

    for metric in payload.data.metrics:
        name = metric.name.lower()
        if name == "sleep_analysis":
            sleep.extend(_build_sleep(metric.data, sleep_source))
        elif name == "heart_rate_variability":
            hrv.extend(_build_hrv(metric.data, sleep_source))
        elif name == "resting_heart_rate":
            rhr.extend(_build_rhr(metric.data, sleep_source))
        # else: ignore (steps, vitals, symptoms, … — not in scope)

    return IngestData(sleep=sleep, hrv=hrv, rhr=rhr)


def _build_sleep(rows: list[dict], sleep_source: str | None) -> list[SleepSample]:
    out: list[SleepSample] = []
    for row in rows:
        src = row.get("source")
        if not _matches(src, sleep_source):
            continue
        stage = STAGE_MAP.get(row.get("value", ""))
        if stage is None:
            continue
        start_raw, end_raw = row.get("start"), row.get("end")
        if not start_raw or not end_raw:
            continue
        start, end = _parse_utc(start_raw), _parse_utc(end_raw)
        if end < start:
            continue
        out.append(
            SleepSample(
                start=start,
                end=end,
                stage=stage,
                source=src,
                source_version=row.get("sourceVersion"),
            )
        )
    return out


def _build_hrv(rows: list[dict], sleep_source: str | None) -> list[HrvSample]:
    out: list[HrvSample] = []
    for row in rows:
        src = row.get("source")
        if not _matches(src, sleep_source):
            continue
        ts_raw = row.get("date") or row.get("start")
        qty = row.get("qty")
        if not ts_raw or qty is None:
            continue
        out.append(HrvSample(timestamp=_parse_utc(ts_raw), value_ms=qty, source=src))
    return out


def _build_rhr(rows: list[dict], sleep_source: str | None) -> list[RhrSample]:
    out: list[RhrSample] = []
    for row in rows:
        src = row.get("source")
        if not _matches(src, sleep_source):
            continue
        date_raw = row.get("date")
        qty = row.get("qty")
        if not date_raw or qty is None:
            continue
        out.append(RhrSample(date=_parse_local_date(date_raw), value_bpm=qty, source=src))
    return out
