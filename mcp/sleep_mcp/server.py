"""sleep-mcp — read-only MCP server exposing HealthBridge sleep data.

Opens the SAME health.duckdb as the ingestion service, but READ-ONLY. Never writes.
Consumed by Claude (claude.ai web connector) and by the cycling-coach MCP.

Auth: this is an OAuth 2.1 Resource Server. Bearer JWTs are issued by the shared
authorization server (mcp-auth) and verified here with the same HS256 signing key.
RemoteAuthProvider serves the RFC 9728 protected-resource metadata pointing at the
AS. Set HEALTHBRIDGE_MCP_DEV=1 to disable auth for localhost-direct dev.
"""

from __future__ import annotations

import os
from datetime import date
from statistics import mean
from zoneinfo import ZoneInfo

import duckdb
from fastmcp import FastMCP

DB_PATH = os.environ.get("HEALTHBRIDGE_DB", "/data/health.duckdb")
# Storage is UTC; display is a read-time concern (CLAUDE.md #4). bed/wake times are
# localized to this zone for humans. v1 assumes Europe/Amsterdam (matches db.py).
LOCAL_TZ = ZoneInfo(os.environ.get("HEALTHBRIDGE_TZ", "Europe/Amsterdam"))

# ── Resource-server / auth config ────────────────────────────────────────────
AUTH_SERVER_URL = os.environ.get("MCP_AUTH_SERVER_URL", "https://mcp-auth.example.com")
PUBLIC_URL = os.environ.get("MCP_PUBLIC_URL", "https://mcp-healthbridge.example.com")
SIGNING_KEY = os.environ.get("MCP_OAUTH_SIGNING_KEY", "")
DEV_MODE = os.environ.get("HEALTHBRIDGE_MCP_DEV") == "1"
PORT = int(os.environ.get("MCP_PORT", "8001"))

# ── Recovery thresholds (tunable) ────────────────────────────────────────────
HRV_FLAG_RATIO = 0.80  # latest HRV below 80% of baseline -> concern
RHR_FLAG_DELTA = 5.0  # latest RHR more than +5 bpm over baseline -> concern
SLEEP_TARGET_HOURS = 8.0
SLEEP_DEBT_FLAG_HOURS = 6.0  # debt over the 7-night window above this -> concern
BASELINE_DAYS = 30  # rolling window for HRV/RHR baselines


def _build_auth():
    """Return a RemoteAuthProvider, or None for local dev / no signing key.

    HS256 tokens come from the external AS (mcp-auth); we verify them with the
    shared secret and advertise the AS via RFC 9728 protected-resource metadata.
    """
    if DEV_MODE or not SIGNING_KEY:
        return None
    from fastmcp.server.auth import RemoteAuthProvider
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    verifier = JWTVerifier(public_key=SIGNING_KEY, algorithm="HS256", issuer=AUTH_SERVER_URL)
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AUTH_SERVER_URL],
        base_url=PUBLIC_URL,
    )


mcp = FastMCP("sleep-mcp", auth=_build_auth())


def _ro_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(DB_PATH, read_only=True)
    # Return/render TIMESTAMPTZ in UTC (we store UTC; keep it UTC on the wire).
    conn.execute("SET TimeZone='UTC'")
    return conn


# nightly_summary columns, in query order.
_NIGHT_COLS = (
    "night_date",
    "bed_time",
    "wake_time",
    "time_in_bed_seconds",
    "asleep_seconds",
    "rem_seconds",
    "deep_seconds",
    "core_seconds",
    "awake_seconds",
    "efficiency_pct",
    "hrv_avg_ms",
    "rhr_bpm",
)
_NIGHT_SELECT = f"SELECT {', '.join(_NIGHT_COLS)} FROM nightly_summary"


def _fmt_hms(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _round(v, n=1):
    return round(v, n) if v is not None else None


def _night_to_dict(row: tuple) -> dict:
    d = dict(zip(_NIGHT_COLS, row, strict=True))
    return {
        "night_date": str(d["night_date"]),
        # Localized to LOCAL_TZ for human reading (stored UTC, converted at read time).
        "bed_time": d["bed_time"].astimezone(LOCAL_TZ).isoformat() if d["bed_time"] else None,
        "wake_time": d["wake_time"].astimezone(LOCAL_TZ).isoformat() if d["wake_time"] else None,
        "time_in_bed_seconds": d["time_in_bed_seconds"],
        "time_in_bed": _fmt_hms(d["time_in_bed_seconds"]),
        "asleep_seconds": d["asleep_seconds"],
        "asleep": _fmt_hms(d["asleep_seconds"]),
        "rem_seconds": d["rem_seconds"],
        "deep_seconds": d["deep_seconds"],
        "core_seconds": d["core_seconds"],
        "awake_seconds": d["awake_seconds"],
        "efficiency_pct": _round(d["efficiency_pct"]),
        "hrv_avg_ms": _round(d["hrv_avg_ms"]),
        "rhr_bpm": _round(d["rhr_bpm"]),
    }


@mcp.tool
def get_latest_night() -> dict:
    """Return the most recent night's full summary (human durations + raw seconds)."""
    conn = _ro_conn()
    try:
        row = conn.execute(f"{_NIGHT_SELECT} ORDER BY night_date DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return _night_to_dict(row) if row else {}


@mcp.tool
def get_nightly_summary(night_date: date) -> dict:
    """Return one night's summary by date (empty dict if absent)."""
    conn = _ro_conn()
    try:
        row = conn.execute(f"{_NIGHT_SELECT} WHERE night_date = ?", [night_date]).fetchone()
    finally:
        conn.close()
    return _night_to_dict(row) if row else {}


@mcp.tool
def get_nightly_range(start_date: date, end_date: date) -> list[dict]:
    """Return summaries for nights in [start_date, end_date] inclusive, ascending."""
    conn = _ro_conn()
    try:
        rows = conn.execute(
            f"{_NIGHT_SELECT} WHERE night_date BETWEEN ? AND ? ORDER BY night_date",
            [start_date, end_date],
        ).fetchall()
    finally:
        conn.close()
    return [_night_to_dict(r) for r in rows]


@mcp.tool
def get_hrv_trend(days: int = 30) -> dict:
    """Return the HRV series for the last `days` nights plus the rolling baseline."""
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT night_date, hrv_avg_ms FROM nightly_summary ORDER BY night_date DESC LIMIT ?",
            [days],
        ).fetchall()
    finally:
        conn.close()
    series = [{"night_date": str(d), "hrv_avg_ms": _round(v)} for d, v in reversed(rows)]
    vals = [v for _, v in rows if v is not None]
    return {
        "days": days,
        # Mean over the returned window, INCLUDING the latest night. Note this differs
        # from get_recovery_status, which excludes the latest night from its baseline.
        "baseline_ms": _round(mean(vals)) if vals else None,
        "baseline_nights": len(vals),
        "baseline_includes_latest": True,
        "series": series,
    }


@mcp.tool
def get_sleep_debt(window_days: int = 7, target_hours: float = SLEEP_TARGET_HOURS) -> dict:
    """Return accumulated sleep deficit vs target over the most recent window.

    Two figures, because oversleep is treated differently:
      - debt_hours: sum of per-night shortfalls, with surplus nights floored at 0
        (oversleep gives NO credit). This is the recovery-relevant number.
      - net_debt_hours: target*nights - total slept (oversleep DOES offset; may be
        negative). This is what summing per_night by hand reproduces.
    """
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT night_date, asleep_seconds FROM nightly_summary "
            "ORDER BY night_date DESC LIMIT ?",
            [window_days],
        ).fetchall()
    finally:
        conn.close()
    per_night = []
    debt = 0.0
    total = 0.0
    for d, asleep in reversed(rows):
        hours = (asleep or 0) / 3600
        total += hours
        debt += max(0.0, target_hours - hours)
        per_night.append({"night_date": str(d), "asleep_hours": round(hours, 2)})
    return {
        "window_days": window_days,
        "target_hours": target_hours,
        "nights_counted": len(rows),
        "debt_hours": round(debt, 1),  # cumulative shortfall, no oversleep credit
        "net_debt_hours": round(target_hours * len(rows) - total, 1),  # may be negative
        "per_night": per_night,
    }


@mcp.tool
def get_recovery_status() -> dict:
    """Composite recovery signal: green/yellow/red with reasoning.

    Combines HRV-vs-baseline, RHR-vs-baseline, and 7-night sleep debt. Baselines
    use the prior nights (excluding the latest) over a rolling window; HRV is
    sparse (~2-3 samples/night) so a multi-night baseline is essential. Number of
    triggered flags maps to status: 0 green, 1 yellow, >=2 red.
    """
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT night_date, asleep_seconds, hrv_avg_ms, rhr_bpm FROM nightly_summary "
            "ORDER BY night_date DESC LIMIT ?",
            [BASELINE_DAYS + 1],
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"status": "unknown", "reasons": ["no data"], "metrics": {}}

    latest = rows[0]
    prior = rows[1:]
    reasons: list[str] = []
    metrics: dict = {}

    # Baselines use the PRIOR nights (latest excluded) so "today vs history" is
    # meaningful. NOTE: get_hrv_trend's baseline INCLUDES the latest night and uses
    # its own window, so the two HRV baselines legitimately differ — both label their
    # window/count to make that explicit.
    metrics["baseline_excludes_latest"] = True

    # HRV vs baseline (lower is worse).
    latest_hrv = latest[2]
    hist_hrv = [r[2] for r in prior if r[2] is not None]
    if latest_hrv is not None and hist_hrv:
        base = mean(hist_hrv)
        metrics["hrv_latest_ms"] = _round(latest_hrv)
        metrics["hrv_baseline_ms"] = _round(base)
        metrics["hrv_baseline_nights"] = len(hist_hrv)
        if base > 0 and latest_hrv < HRV_FLAG_RATIO * base:
            reasons.append(
                f"HRV {latest_hrv:.0f}ms is below {HRV_FLAG_RATIO:.0%} of the "
                f"{base:.0f}ms baseline ({len(hist_hrv)}-night)"
            )

    # RHR vs baseline (higher is worse).
    latest_rhr = latest[3]
    hist_rhr = [r[3] for r in prior if r[3] is not None]
    if latest_rhr is not None and hist_rhr:
        base = mean(hist_rhr)
        metrics["rhr_latest_bpm"] = _round(latest_rhr)
        metrics["rhr_baseline_bpm"] = _round(base)
        metrics["rhr_baseline_nights"] = len(hist_rhr)
        if latest_rhr > base + RHR_FLAG_DELTA:
            reasons.append(
                f"RHR {latest_rhr:.0f}bpm is more than {RHR_FLAG_DELTA:.0f} over the "
                f"{base:.0f}bpm baseline ({len(hist_rhr)}-night)"
            )

    # Sleep debt over the last 7 nights (cumulative shortfall, no oversleep credit).
    last7 = rows[:7]
    debt = sum(max(0.0, SLEEP_TARGET_HOURS - (r[1] or 0) / 3600) for r in last7)
    metrics["sleep_debt_hours"] = round(debt, 1)
    metrics["sleep_debt_window_nights"] = len(last7)
    if debt > SLEEP_DEBT_FLAG_HOURS:
        reasons.append(f"sleep debt {debt:.1f}h over the last {len(last7)} nights")

    status = "green" if not reasons else "yellow" if len(reasons) == 1 else "red"
    return {"status": status, "reasons": reasons, "metrics": metrics}


# ── Discovery aliases (unauthenticated) ──────────────────────────────────────
# claude.ai (and other clients) probe the host root and the ROOT-level RFC 9728
# document before falling back to the WWW-Authenticate pointer. FastMCP only serves
# the path-scoped /.well-known/oauth-protected-resource/mcp, so we add a root-level
# alias for broader client compatibility, plus a / liveness to quiet base-URL probes.


@mcp.custom_route("/", methods=["GET", "HEAD", "POST"])
async def _root_liveness(request):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok"})


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def _protected_resource_root(request):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "resource": f"{PUBLIC_URL}/mcp",
            "authorization_servers": [AUTH_SERVER_URL],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [],
        }
    )


def build_app():
    """Build the Streamable-HTTP ASGI app with CORS for browser MCP clients.

    CORS must wrap the app as the outermost layer so OPTIONS preflight is answered
    before auth (preflight carries no credentials). WWW-Authenticate is exposed so
    the browser can read the RFC 9728 resource_metadata pointer off a 401.
    """
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    cors = Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Mcp-Session-Id",
            "Mcp-Protocol-Version",
            "Last-Event-ID",
        ],
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
        max_age=86400,
    )
    return mcp.http_app(middleware=[cors])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=PORT)
