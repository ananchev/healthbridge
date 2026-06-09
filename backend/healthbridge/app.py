"""HealthBridge ingestion service (FastAPI).

Runs behind Nginx-Proxy-Manager (NPM). The backend owns authentication via a
bearer token (see auth.py) — there is NO Cloudflare Access. The phone sends
`Authorization: Bearer <HEALTHBRIDGE_TOKEN>` on every request.

NOTE for Claude Code: endpoint bodies are stubs. Implement using db.py + auth.py.
Keep the app thin — validation via Pydantic, persistence via db.py, auth via auth.py.
Follow the TDD discipline in CLAUDE.md: write/extend tests in tests/ BEFORE or
alongside each endpoint, and do not consider an endpoint done until its tests pass.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import auth, dashboard, db, hae_adapter
from .hae_adapter import HAEPayload
from .models import IngestData, IngestPayload, IngestResult

DB_PATH = os.environ.get("HEALTHBRIDGE_DB", "/data/health.duckdb")
STATIC_DIR = Path(__file__).parent / "static"
# The dashboard is LAN-only and UNAUTHENTICATED. It is served only to requests that
# reach the app directly on the LAN (IP:8000). Set to "1" ONLY if you intend to expose
# it publicly behind your own auth — otherwise leave it off so it stays LAN-only.
DASHBOARD_PUBLIC = os.environ.get("HEALTHBRIDGE_DASHBOARD_PUBLIC") == "1"
# Proxy forwarding headers. Their presence means the request came THROUGH NPM (i.e. from
# the public internet); a direct LAN hit to :8000 carries none of these.
_FORWARDED_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")
APP_ENV = os.environ.get("APP_ENV", "prod")
# Sleep source to keep (Apple Watch). Matching is NBSP-insensitive (see hae_adapter).
# Configurable per CLAUDE.md constraint #3 — never hardcoded. None = keep all sources.
SLEEP_SOURCE = os.environ.get("HEALTHBRIDGE_SLEEP_SOURCE") or None
# Reject request bodies larger than this before parsing (memory-exhaustion guard on
# the public POST surface). Legit HAE exports are a few MB; default 25 MB is generous.
MAX_BODY_BYTES = int(os.environ.get("HEALTHBRIDGE_MAX_BODY_BYTES", str(25 * 1024 * 1024)))

# Applied to every response. This is a JSON API for a private app — no caching of
# health data, no sniffing, no framing, no referrer leakage.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    conn.close()
    yield


# Interactive docs and the OpenAPI schema are DISABLED: this is not a public API for
# third parties, and exposing the schema would enumerate endpoints/payload shapes.
app = FastAPI(
    title="HealthBridge Ingestion",
    version="1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Body-size guard (pre-parse) + security headers + server-banner strip."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if declared > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Payload too large"}, status_code=413)

    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        if name not in response.headers:
            response.headers[name] = value
    if "server" in response.headers:
        del response.headers["server"]
    return response


@app.api_route("/", methods=["GET", "HEAD", "POST"])
def root() -> dict[str, str]:
    # Open liveness/preflight. Some clients (e.g. Health Auto Export) POST the host
    # root to check reachability before sending data. No auth, no DB, no info leak.
    return {"status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    # Liveness only — no auth, no DB. Used by NPM/uptime checks.
    return {"status": "ok"}


def _persist(data: IngestData) -> IngestResult:
    """Dedupe-write a normalized batch and recompute affected nights.

    Shared by /ingest (native wire format) and /ingest/hae (Health Auto Export).
    db.py remains the single writer; both paths converge here.
    """
    conn = db.connect(DB_PATH)
    try:
        sleep_written = db.insert_sleep(conn, data.sleep)
        hrv_written = db.insert_hrv(conn, data.hrv)
        rhr_written = db.insert_rhr(conn, data.rhr)

        nights: set[date] = set()
        nights.update(db.affected_nights_from_sleep(data.sleep))
        nights.update(db.assign_to_night(h.timestamp) for h in data.hrv)
        nights.update(r.date for r in data.rhr)

        recomputed = db.recompute_nights(conn, nights) if nights else []

        latest_sleep = conn.execute("SELECT MAX(start_ts) FROM sleep_samples").fetchone()[0]
        latest_hrv = conn.execute("SELECT MAX(ts) FROM hrv_samples").fetchone()[0]
        latest_rhr = conn.execute("SELECT MAX(date) FROM rhr_samples").fetchone()[0]

        return IngestResult(
            sleep_written=sleep_written,
            hrv_written=hrv_written,
            rhr_written=rhr_written,
            nights_recomputed=recomputed,
            latest_sleep_ts=latest_sleep,
            latest_hrv_ts=latest_hrv,
            latest_rhr_date=latest_rhr,
        )
    finally:
        conn.close()


@app.post("/ingest", response_model=IngestResult)
def ingest(
    payload: IngestPayload,
    authorization: str | None = Header(default=None),
) -> IngestResult:
    """Validate bearer, dedupe-write, recompute affected nights."""
    auth.verify_bearer(authorization)
    return _persist(payload.data)


@app.post("/ingest/hae", response_model=IngestResult)
def ingest_hae(
    payload: HAEPayload,
    authorization: str | None = Header(default=None),
) -> IngestResult:
    """Ingest a Health Auto Export payload: normalize to canonical IngestData
    (filter to the Apple Watch source, map stages, convert to UTC), then persist
    via the same write path as /ingest. See docs/HAE_SETUP.md."""
    auth.verify_bearer(authorization)
    data = hae_adapter.normalize(payload, sleep_source=SLEEP_SOURCE)
    return _persist(data)


# ── Monitoring dashboard (LAN-only, UNAUTHENTICATED) ──────────────────────────
# These two routes are deliberately open BUT fail closed: `_require_lan` refuses any
# request that arrives through the proxy (i.e. from the public internet), so the
# dashboard is reachable ONLY by hitting the app directly on the LAN (IP:8000). Safety
# therefore does NOT depend on the NPM deny rule (that stays as defense-in-depth) or on
# this repo being private — an attacker who knows /dashboard exists still cannot reach
# it from the internet. The routes are READ-ONLY (SELECTs only); the single-writer rule
# and the /ingest auth surface are untouched. See deploy/NETWORKING.md.


def _require_lan(request: Request) -> None:
    """Fail closed: serve the dashboard only to direct LAN requests.

    NPM stamps forwarding headers on everything it proxies, and :8000 is never
    internet-reachable, so a request carrying any forwarding header came from the
    public side and is refused with 404 (don't even confirm the route exists).
    `HEALTHBRIDGE_DASHBOARD_PUBLIC=1` opts out (only if fronted by your own auth).
    """
    if DASHBOARD_PUBLIC:
        return
    if any(h in request.headers for h in _FORWARDED_HEADERS):
        raise HTTPException(status_code=404)


@app.get("/dashboard")
def dashboard_page(_: None = Depends(_require_lan)) -> FileResponse:
    """Serve the self-contained monitoring UI (LAN-only)."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/dashboard/data")
def dashboard_data(
    window: int = Query(default=7),
    end: date | None = Query(default=None),
    _: None = Depends(_require_lan),
) -> dict:
    """Windowed nightly parameters + registry + per-metric trends for the UI (LAN-only)."""
    conn = db.connect(DB_PATH)
    try:
        result = dashboard.fetch_window(conn, end, window)
    finally:
        conn.close()
    return {
        **result,
        "metrics": dashboard.metrics_as_dicts(),
        "trends": dashboard.compute_trends(result["nights"]),
        "window_avg": dashboard.window_averages(result["nights"]),
    }


@app.get("/stats")
def stats(authorization: str | None = Header(default=None)) -> dict:
    """Per-type counts, latest timestamps, freshness. Includes app_env for banner."""
    auth.verify_bearer(authorization)
    conn = db.connect(DB_PATH)
    try:
        sleep_count = conn.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0]
        hrv_count = conn.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0]
        rhr_count = conn.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0]
        latest_sleep = conn.execute("SELECT MAX(start_ts) FROM sleep_samples").fetchone()[0]
        latest_hrv = conn.execute("SELECT MAX(ts) FROM hrv_samples").fetchone()[0]
        latest_rhr = conn.execute("SELECT MAX(date) FROM rhr_samples").fetchone()[0]
        latest_night = conn.execute("SELECT MAX(night_date) FROM nightly_summary").fetchone()[0]
        return {
            "sleep_count": sleep_count,
            "hrv_count": hrv_count,
            "rhr_count": rhr_count,
            "latest_sleep_ts": latest_sleep,
            "latest_hrv_ts": latest_hrv,
            "latest_rhr_date": str(latest_rhr) if latest_rhr else None,
            "latest_night_date": str(latest_night) if latest_night else None,
            "app_env": APP_ENV,
        }
    finally:
        conn.close()
