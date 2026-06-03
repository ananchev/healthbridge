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

from fastapi import FastAPI, Header

from . import auth, db
from .models import IngestPayload, IngestResult

DB_PATH = os.environ.get("HEALTHBRIDGE_DB", "/data/health.duckdb")
APP_ENV = os.environ.get("APP_ENV", "prod")


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    conn.close()
    yield


app = FastAPI(title="HealthBridge Ingestion", version="1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    # Liveness only — no auth, no DB. Used by NPM/uptime checks.
    return {"status": "ok"}


@app.post("/ingest", response_model=IngestResult)
def ingest(
    payload: IngestPayload,
    authorization: str | None = Header(default=None),
) -> IngestResult:
    """Validate bearer, dedupe-write, recompute affected nights."""
    auth.verify_bearer(authorization)
    conn = db.connect(DB_PATH)
    try:
        sleep_written = db.insert_sleep(conn, payload.data.sleep)
        hrv_written = db.insert_hrv(conn, payload.data.hrv)
        rhr_written = db.insert_rhr(conn, payload.data.rhr)

        nights: set[date] = set()
        nights.update(db.affected_nights_from_sleep(payload.data.sleep))
        nights.update(db.assign_to_night(h.timestamp) for h in payload.data.hrv)
        nights.update(r.date for r in payload.data.rhr)

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
