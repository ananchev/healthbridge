"""Contract tests for sleep-mcp tools (read-only).

Per docs/TESTING.md rule 7: seed a temp DuckDB, call each tool function directly,
assert shape + values. get_recovery_status needs green/yellow/red cases.

NOTE for Claude Code: build a small fixture that creates the schema and inserts a
handful of nights/HRV/RHR rows, then point the tool at that DB (param or env).
"""

from __future__ import annotations

import pytest


def test_get_latest_night(tmp_path):
    pytest.skip("implement once tools query real data")


def test_get_nightly_range(tmp_path):
    pytest.skip("implement")


def test_recovery_status_green(tmp_path):
    pytest.skip("implement")


def test_recovery_status_yellow(tmp_path):
    pytest.skip("implement")


def test_recovery_status_red(tmp_path):
    pytest.skip("implement")
