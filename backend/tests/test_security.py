"""Security posture tests for the public ingestion surface.

Covers: root liveness (HAE preflight) leaks nothing, API schema/docs are disabled,
security headers are present, server banner is stripped, and oversized request
bodies are rejected before parsing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import healthbridge.app as app_module
import healthbridge.auth as auth_module
from healthbridge.app import app

TOKEN = "testtoken"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", str(tmp_path / "t.duckdb"))
    monkeypatch.setattr(auth_module, "DEV_MODE", False)
    monkeypatch.setattr(auth_module, "EXPECTED_TOKEN", TOKEN)
    with TestClient(app) as c:
        yield c


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


# --- root liveness (Health Auto Export posts the host root as a reachability check) ---


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_root_liveness_open(client, method):
    resp = client.request(method, "/")
    assert resp.status_code == 200


def test_root_no_auth_required(client):
    # Preflight has no payload, so it must not require the bearer token.
    assert client.post("/").status_code == 200


def test_root_body_reveals_nothing(client):
    # Only a generic status — no app name, version, or framework details.
    assert client.get("/").json() == {"status": "ok"}


# --- API schema / interactive docs disabled (no endpoint enumeration) ---


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_api_schema_disabled(client, path):
    assert client.get(path).status_code == 404


# --- security headers on every response ---


def test_security_headers_present(client):
    h = client.get("/health").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["Cache-Control"] == "no-store"
    assert h["X-Frame-Options"] == "DENY"


def test_server_banner_stripped(client):
    header_names = {k.lower() for k in client.get("/health").headers}
    assert "server" not in header_names


# --- request body size guard (memory-exhaustion DoS protection) ---


def test_oversized_body_rejected(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_BODY_BYTES", 10)
    resp = client.post("/ingest/hae", json={"data": {"metrics": []}}, headers=_auth())
    assert resp.status_code == 413


def test_invalid_content_length_rejected(client):
    resp = client.post(
        "/ingest/hae",
        content=b"{}",
        headers={**_auth(), "Content-Length": "not-a-number", "Content-Type": "application/json"},
    )
    # Starlette/our guard must not 500 on a malformed Content-Length.
    assert resp.status_code in (400, 422)


def test_normal_body_under_limit_ok(client):
    resp = client.post("/ingest/hae", json={"data": {"metrics": []}}, headers=_auth())
    assert resp.status_code == 200
