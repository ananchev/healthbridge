"""Tests for bearer-token auth (auth.py).

Per docs/TESTING.md rule 5: cover missing header, malformed header, wrong token,
correct token, dev bypass, and unconfigured-token-in-prod.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from healthbridge import auth


def test_dev_mode_bypasses(monkeypatch):
    """HEALTHBRIDGE_DEV=1 -> verify_bearer returns without raising."""
    monkeypatch.setattr(auth, "DEV_MODE", True)
    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "secret")
    auth.verify_bearer(None)  # no exception


def test_missing_header_401(monkeypatch):
    monkeypatch.setattr(auth, "DEV_MODE", False)
    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_bearer(None)
    assert exc_info.value.status_code == 401


def test_malformed_header_401(monkeypatch):
    """Header not starting with 'Bearer ' -> 401."""
    monkeypatch.setattr(auth, "DEV_MODE", False)
    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_bearer("Token abc")
    assert exc_info.value.status_code == 401


def test_wrong_token_403(monkeypatch):
    monkeypatch.setattr(auth, "DEV_MODE", False)
    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_bearer("Bearer wrong")
    assert exc_info.value.status_code == 403


def test_correct_token_ok(monkeypatch):
    monkeypatch.setattr(auth, "DEV_MODE", False)
    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "secret")
    auth.verify_bearer("Bearer secret")  # no exception


def test_unconfigured_token_in_prod_errors(monkeypatch):
    """EXPECTED_TOKEN empty and not dev -> refuse (500-class misconfig)."""
    monkeypatch.setattr(auth, "DEV_MODE", False)
    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "")
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_bearer("Bearer something")
    assert exc_info.value.status_code == 500
