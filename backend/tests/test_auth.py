"""Tests for bearer-token auth (auth.py).

Per docs/TESTING.md rule 5: cover missing header, malformed header, wrong token,
correct token, dev bypass, and unconfigured-token-in-prod.

NOTE for Claude Code: implement these by monkeypatching auth.EXPECTED_TOKEN /
auth.DEV_MODE (or reload with env). Assert the exact HTTPException status codes.
"""

from __future__ import annotations

import pytest


def test_dev_mode_bypasses(monkeypatch):
    """HEALTHBRIDGE_DEV=1 -> verify_bearer returns without raising."""
    pytest.skip("implement once auth.verify_bearer is written")


def test_missing_header_401():
    pytest.skip("implement")


def test_malformed_header_401():
    """Header not starting with 'Bearer ' -> 401."""
    pytest.skip("implement")


def test_wrong_token_403():
    pytest.skip("implement")


def test_correct_token_ok():
    pytest.skip("implement")


def test_unconfigured_token_in_prod_errors():
    """EXPECTED_TOKEN empty and not dev -> refuse (500-class misconfig)."""
    pytest.skip("implement")
