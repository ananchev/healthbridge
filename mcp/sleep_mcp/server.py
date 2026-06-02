"""sleep-mcp — read-only MCP server exposing HealthBridge sleep data.

Opens the SAME health.duckdb as the ingestion service, but READ-ONLY. Never writes.
Consumed by Claude and by the existing cycling-coach MCP.

NOTE for Claude Code: tool bodies are stubs. Implement queries against the
nightly_summary table (and raw tables for trends). Use FastMCP. Keep everything
read-only: db.connect(path, read_only=True).

Recovery status logic (get_recovery_status):
  - HRV: compare latest night hrv_avg_ms to 30-day rolling mean. Below ~0.8x -> flag.
  - RHR: compare latest rhr_bpm to 30-day baseline. Elevated -> flag.
  - Sleep debt: sum of (target - asleep) over last 7 nights.
  - Combine into green / yellow / red with a short human-readable reason string.
  Thresholds should be module-level constants so they're easy to tune.
"""

from __future__ import annotations

import os
from datetime import date

from fastmcp import FastMCP

# Read-only DuckDB connection helper. Reuse backend's connect with read_only=True,
# or open duckdb directly here to avoid importing the writer package.
import duckdb

DB_PATH = os.environ.get("HEALTHBRIDGE_DB", "/data/health.duckdb")

# Recovery thresholds (tunable)
HRV_FLAG_RATIO = 0.80      # latest HRV below 80% of baseline -> concern
RHR_FLAG_DELTA = 5.0       # latest RHR more than +5 bpm over baseline -> concern
SLEEP_TARGET_HOURS = 8.0

mcp = FastMCP("sleep-mcp")


def _ro_conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=True)


@mcp.tool()
def get_latest_night() -> dict:
    """Return the most recent night's full summary.

    TODO(claude-code): SELECT * FROM nightly_summary ORDER BY night_date DESC LIMIT 1.
    Return as a dict with human-friendly duration formatting (e.g. '6h47m') plus
    raw seconds for programmatic use.
    """
    raise NotImplementedError


@mcp.tool()
def get_nightly_summary(night_date: date) -> dict:
    """Return one night's summary by date."""
    raise NotImplementedError


@mcp.tool()
def get_nightly_range(start_date: date, end_date: date) -> list[dict]:
    """Return summaries for nights in [start_date, end_date] inclusive."""
    raise NotImplementedError


@mcp.tool()
def get_hrv_trend(days: int = 30) -> dict:
    """Return HRV series for the last `days` nights plus a rolling baseline."""
    raise NotImplementedError


@mcp.tool()
def get_sleep_debt(window_days: int = 7, target_hours: float = SLEEP_TARGET_HOURS) -> dict:
    """Return accumulated sleep deficit vs target over the window."""
    raise NotImplementedError


@mcp.tool()
def get_recovery_status() -> dict:
    """Composite recovery signal: green/yellow/red with reasoning.

    Combine HRV-vs-baseline, RHR-vs-baseline, and recent sleep debt. See module
    docstring for the logic sketch. Return:
      {"status": "green|yellow|red", "reasons": [...], "metrics": {...}}.
    """
    raise NotImplementedError


if __name__ == "__main__":
    mcp.run()
