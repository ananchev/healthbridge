"""Tests for the Health Auto Export (HAE) adapter + /ingest/hae endpoint.

Fixture (tests/fixtures/hae_sample.json) is synthetic but mirrors the real export
shape: NBSP Apple Watch source, a SleepWatch row that must be filtered, short stage
strings, local-offset timestamps, near-midnight RHR, and an out-of-scope metric.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import healthbridge.app as app_module
import healthbridge.auth as auth_module
from healthbridge.app import app
from healthbridge.hae_adapter import HAEPayload, normalize

FIXTURE = Path(__file__).parent / "fixtures" / "hae_sample.json"
# Configured source uses a REGULAR space; the fixture source has a NON-BREAKING
# space. Matching must be NBSP-insensitive.
SLEEP_SOURCE = "Apple Watch van Owner"
NBSP_SOURCE = "Apple Watch van Owner"
TOKEN = "testtoken"


@pytest.fixture
def payload() -> HAEPayload:
    return HAEPayload.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


# --- adapter: source filtering ------------------------------------------------


def test_filters_sleepwatch_out(payload):
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    sources = {s.source for s in data.sleep}
    assert sources == {NBSP_SOURCE}  # SleepWatch dropped
    assert len(data.sleep) == 5


def test_no_filter_keeps_all_sources(payload):
    data = normalize(payload, sleep_source=None)
    assert any(s.source == "SleepWatch" for s in data.sleep)
    assert len(data.sleep) == 6


def test_source_stored_verbatim_with_nbsp(payload):
    """Match is NBSP-insensitive, but the original NBSP string is stored."""
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    assert all(s.source == NBSP_SOURCE for s in data.sleep)
    assert all(" " in s.source for s in data.sleep)


# --- adapter: stage mapping ---------------------------------------------------


def test_stage_mapping(payload):
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    stages = sorted(s.stage for s in data.sleep)
    assert stages == sorted(["AsleepCore", "Awake", "AsleepDeep", "AsleepREM", "AsleepUnspecified"])


# --- adapter: timestamp handling ----------------------------------------------


def test_sleep_timestamps_converted_to_utc(payload):
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    core = next(s for s in data.sleep if s.stage == "AsleepCore")
    # 2026-05-29 01:09:08 +0200 == 2026-05-28 23:09:08 UTC
    assert core.start == datetime(2026, 5, 28, 23, 9, 8, tzinfo=UTC)


def test_hrv_mapped(payload):
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    assert len(data.hrv) == 2
    first = data.hrv[0]
    assert first.value_ms == 31.5
    # 00:08:57 +0200 == previous day 22:08:57 UTC
    assert first.timestamp == datetime(2026, 5, 28, 22, 8, 57, tzinfo=UTC)


def test_rhr_keyed_by_local_date_not_utc(payload):
    """RHR date is local midnight (00:00:38 +0200); must stay 2026-05-29, NOT shift
    to 2026-05-28 as a naive UTC conversion would."""
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    assert len(data.rhr) == 1
    assert data.rhr[0].date == date(2026, 5, 29)
    assert data.rhr[0].value_bpm == 57


# --- adapter: out-of-scope metrics --------------------------------------------


def test_unknown_metric_ignored(payload):
    """An all-on export with steps/vitals/etc. must not crash or leak in."""
    data = normalize(payload, sleep_source=SLEEP_SOURCE)
    # Only the three in-scope types are produced; 'steps' silently ignored.
    assert len(data.sleep) == 5
    assert len(data.hrv) == 2
    assert len(data.rhr) == 1


# --- endpoint: /ingest/hae ----------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setattr(app_module, "SLEEP_SOURCE", SLEEP_SOURCE)
    monkeypatch.setattr(auth_module, "DEV_MODE", False)
    monkeypatch.setattr(auth_module, "EXPECTED_TOKEN", TOKEN)
    with TestClient(app) as c:
        yield c


def _hae_body() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_ingest_hae_writes_and_filters_source(client):
    resp = client.post("/ingest/hae", json=_hae_body(), headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_written"] == 5  # SleepWatch row excluded
    assert data["hrv_written"] == 2
    assert data["rhr_written"] == 1
    assert len(data["nights_recomputed"]) == 1


def test_ingest_hae_is_idempotent(client):
    """Mandatory write-path idempotency: second identical POST writes 0 rows."""
    client.post("/ingest/hae", json=_hae_body(), headers=_auth())
    resp = client.post("/ingest/hae", json=_hae_body(), headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_written"] == 0
    assert data["hrv_written"] == 0
    assert data["rhr_written"] == 0


def test_ingest_hae_missing_auth_401(client):
    resp = client.post("/ingest/hae", json=_hae_body())
    assert resp.status_code == 401


def test_ingest_hae_wrong_token_403(client):
    resp = client.post("/ingest/hae", json=_hae_body(), headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403
