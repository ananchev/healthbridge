"""Pydantic v2 models for the /ingest wire format.

All timestamps are UTC. See docs/SPEC.md section 5.

NOTE for Claude Code: these are STUBS defining the contract. Flesh out validators
(e.g. end_ts >= start_ts, known stage values, value ranges) when implementing.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

# Known sleep stage values (prefix HKCategoryValueSleepAnalysis already stripped;
# the HAE adapter maps short stage names to these — see hae_adapter.py).
SLEEP_STAGES = {
    "AsleepCore",
    "AsleepDeep",
    "AsleepREM",
    "AsleepUnspecified",
    "Awake",
    "InBed",
}

ASLEEP_STAGES = {"AsleepCore", "AsleepDeep", "AsleepREM", "AsleepUnspecified"}


class SleepSample(BaseModel):
    start: datetime
    end: datetime
    stage: str
    source: str
    source_version: str | None = None

    @field_validator("stage")
    @classmethod
    def stage_must_be_known(cls, v: str) -> str:
        if v not in SLEEP_STAGES:
            raise ValueError(f"Unknown sleep stage: {v!r}")
        return v

    @model_validator(mode="after")
    def end_after_start(self) -> SleepSample:
        if self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class HrvSample(BaseModel):
    timestamp: datetime
    value_ms: float = Field(gt=0)
    source: str


class RhrSample(BaseModel):
    date: date
    value_bpm: float = Field(gt=0)
    source: str


class IngestData(BaseModel):
    sleep: list[SleepSample] = Field(default_factory=list)
    hrv: list[HrvSample] = Field(default_factory=list)
    rhr: list[RhrSample] = Field(default_factory=list)


class IngestPayload(BaseModel):
    device_id: str
    synced_at: datetime
    data: IngestData


class IngestResult(BaseModel):
    """Returned from POST /ingest."""

    sleep_written: int = 0
    hrv_written: int = 0
    rhr_written: int = 0
    nights_recomputed: list[date] = Field(default_factory=list)
    latest_sleep_ts: datetime | None = None
    latest_hrv_ts: datetime | None = None
    latest_rhr_date: date | None = None
