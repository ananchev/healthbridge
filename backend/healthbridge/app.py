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
    """Validate bearer, dedupe-write, recompute affected nights.

    TODO(claude-code):
      1. auth.verify_bearer(authorization)
      2. conn = db.connect(DB_PATH)  (RW)
      3. counts = insert_sleep/hrv/rhr
      4. nights = affected nights from sleep + hrv/rhr dates
      5. recomputed = db.recompute_nights(conn, nights)
      6. return IngestResult(counts, recomputed, latest timestamps)
    Tests: idempotency (insert twice -> 0 new), auth branches, summary correctness.
    """
    raise NotImplementedError


@app.get("/stats")
def stats(authorization: str | None = Header(default=None)) -> dict:
    """Per-type counts, latest timestamps, freshness. Includes app_env for banner.

    TODO(claude-code): auth.verify_bearer(authorization); SELECT counts + max
    timestamps per raw table + latest nightly_summary.night_date. Include
    {"app_env": APP_ENV} so a UI can render the non-prod banner.
    """
    raise NotImplementedError
