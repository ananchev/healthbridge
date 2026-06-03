"""Endpoint tests for /health, /ingest, /stats via FastAPI TestClient.

Covers auth branches, success paths, idempotency, empty payload.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import healthbridge.app as app_module
import healthbridge.auth as auth_module
from healthbridge.app import app

TOKEN = "testtoken"

PAYLOAD = {
    "device_id": "test-device",
    "synced_at": "2026-05-20T22:14:00Z",
    "data": {
        "sleep": [
            {
                "start": "2026-05-20T20:00:00Z",
                "end": "2026-05-21T04:00:00Z",
                "stage": "InBed",
                "source": "Apple Watch test",
            }
        ],
        "hrv": [
            {
                "timestamp": "2026-05-20T21:00:00Z",
                "value_ms": 34.2,
                "source": "Apple Watch test",
            }
        ],
        "rhr": [{"date": "2026-05-21", "value_bpm": 55, "source": "Apple Watch test"}],
    },
}

EMPTY_PAYLOAD = {
    "device_id": "test-device",
    "synced_at": "2026-05-20T22:14:00Z",
    "data": {"sleep": [], "hrv": [], "rhr": []},
}


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setattr(auth_module, "DEV_MODE", False)
    monkeypatch.setattr(auth_module, "EXPECTED_TOKEN", TOKEN)
    with TestClient(app) as c:
        yield c


# --- /health ---


def test_health_open(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- /ingest success ---


def test_ingest_success(client):
    resp = client.post("/ingest", json=PAYLOAD, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_written"] == 1
    assert data["hrv_written"] == 1
    assert data["rhr_written"] == 1
    assert len(data["nights_recomputed"]) == 1


def test_ingest_second_post_zero_new(client):
    """Idempotency end-to-end: second POST of same payload writes 0 rows."""
    client.post("/ingest", json=PAYLOAD, headers=_auth())
    resp = client.post("/ingest", json=PAYLOAD, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_written"] == 0
    assert data["hrv_written"] == 0
    assert data["rhr_written"] == 0


def test_ingest_empty_payload(client):
    resp = client.post("/ingest", json=EMPTY_PAYLOAD, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_written"] == 0
    assert data["hrv_written"] == 0
    assert data["rhr_written"] == 0
    assert data["nights_recomputed"] == []


def test_ingest_returns_latest_timestamps(client):
    resp = client.post("/ingest", json=PAYLOAD, headers=_auth())
    data = resp.json()
    assert data["latest_sleep_ts"] is not None
    assert data["latest_hrv_ts"] is not None
    assert data["latest_rhr_date"] is not None


# --- /ingest auth ---


def test_ingest_missing_auth_401(client):
    resp = client.post("/ingest", json=PAYLOAD)
    assert resp.status_code == 401


def test_ingest_wrong_token_403(client):
    resp = client.post("/ingest", json=PAYLOAD, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


# --- /stats ---


def test_stats_success(client):
    client.post("/ingest", json=PAYLOAD, headers=_auth())
    resp = client.get("/stats", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_count"] == 1
    assert data["hrv_count"] == 1
    assert data["rhr_count"] == 1
    assert "app_env" in data


def test_stats_missing_auth_401(client):
    resp = client.get("/stats")
    assert resp.status_code == 401
