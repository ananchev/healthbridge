# Build Order & Acceptance Criteria

Follow in sequence. Each phase has a concrete "done" check. Do not start a phase
until the previous one's check passes.

**TDD gate (applies to every phase):** per `docs/TESTING.md`, write/extend tests
for each unit of logic, run them red, implement to green. `pytest` + `ruff` must be
green before a phase is "done". Idempotency tests are mandatory on write paths. Do
not advance on red.

**Topology reminder (see `deploy/NETWORKING.md`):** Internet → Cloudflare (DNS
proxy) → router :443 → NPM container → `<LAN-IP>:port`. Dev runs the backend on the
laptop (dedicated venv, uvicorn) and flips NPM's upstream to the laptop's LAN IP, so
the real public path exercises local code. Prod is the same path with the upstream
flipped to a Docker host's LAN IP. Cloudflare is DNS-only (no Access, no Tunnel);
the backend owns bearer-token auth.

## Phase 1 — Backend core

Implement:
- `backend/healthbridge/db.py`: insert_sleep / insert_hrv / insert_rhr
  (idempotent), assign_to_night, recompute_nights.
- `backend/healthbridge/auth.py`: verify_bearer (all branches).
- `backend/healthbridge/app.py`: /ingest, /stats (bearer-protected), /health (open).
- `backend/healthbridge/models.py`: finish validators.

**Done when:**
- `pytest` passes, including `test_ingest_is_idempotent` (insert twice → no dupes)
  and `test_auth.py` (all branches).
- Locally (HEALTHBRIDGE_DEV=1) `uvicorn` runs; `curl /health` returns ok.
- A hand-crafted `curl` POST to `/ingest` writes rows and returns correct counts;
  re-POSTing the same body writes 0 new rows.
- `/stats` reflects the written data and reports `app_env`.
- `ruff check` + `ruff format --check` clean.

## Phase 2 — Bootstrap

Verify the (already-written) parser wires to the now-implemented db.py functions.

**Done when:**
- `python -m bootstrap.import_export <real export.zip> --db ./dev.duckdb` runs to
  completion, prints sane counts (thousands of sleep, hundreds of hrv/rhr).
- `nightly_summary` is populated for all historical nights.
- Re-running the bootstrap writes 0 new rows (idempotent).
- Spot-check (encode as a skip-if-absent test): the last 7 nights' asleep/efficiency
  match Owner's validated numbers (≈6h47m avg asleep, 90–98% efficiency for the
  sample week).

## Phase 3 — Dev/e2e path: CF → NPM → laptop (NPM-flip)

The PRIMARY development path, not prod-only. Implement the NPM API calls in
`scripts/dev/npm-flip.sh` (get_token, read_proxy, put_proxy, writable_only) and the
watchdog. Mirror cycling-coach: full-state rollback, flip top-level + per-location
forward_host, wholesale-PUT restore, legacy-string backward-compat. Backend + MCP
run on the laptop in a dedicated venv (.venv-dev) via uvicorn/python — NO Docker.

**Done when:**
- `start-dev-stack.sh --no-flip` runs backend+mcp in .venv-dev against ./dev.duckdb;
  `/health` ok on `localhost:8000` (set HEALTHBRIDGE_DEV=1 to bypass auth on this
  localhost-direct path only).
- `npm-flip.sh status` lists current upstreams incl. per-location overrides.
- `npm-flip.sh laptop <laptop-LAN-IP>` flips both proxies + locations; `prod`
  restores exactly (saved object round-trips).
- `start-dev-stack.sh` (with flip) makes `https://healthbridge.example.com/health` hit
  local uvicorn through the REAL path (Cloudflare → NPM → laptop). `/ingest` with
  `Authorization: Bearer <token>` works; without it → 401 (real auth exercised).
- exit/Ctrl-C reverts NPM; the watchdog reverts even on a hard kill.
- Shell scripts pass `shellcheck`.

## Phase 4 — iOS app (manual sync first)

Implement Swift files per `ios/CLAUDE.md`. Manual path before background. The app
targets the stable `https://healthbridge.example.com`, which Phase 3 flips to the
laptop — so iOS work validates against local code through the real path.

**Done when (manual):**
- App requests HealthKit read auth for the 3 types.
- Source picker lists sources writing sleep; Owner selects his Apple Watch.
- "Sync now" reads last-N-days samples and POSTs with `Authorization: Bearer
  <token>`; backend rows appear; cursors advance; UI shows last-sync + counts.

**Done when (background, after manual works):**
- BGTaskScheduler daily task registered and schedules.
- HKObserverQuery enabled per type; new samples trigger a sync (best-effort on free
  provisioning).

## Phase 5 — Sleep MCP

Implement `mcp/sleep_mcp/server.py` tools (read-only DuckDB). In dev, run it in
.venv-dev alongside the backend.

**Done when:**
- `get_latest_night()` returns the correct most-recent summary.
- `get_nightly_range(start,end)` returns the expected rows.
- `get_recovery_status()` returns green/yellow/red with reasoning derived from
  HRV-vs-baseline + RHR-vs-baseline + 7-night sleep debt.
- `mcp/tests/test_tools.py` green for all tools incl. the three recovery cases.
- The existing cycling-coach MCP can reach its tools (or read the same DuckDB
  read-only) for joins.

## Phase 6 — Prod deploy (only after laptop e2e is fully green)

Containerize and run on a Docker host. Implement: confirm Dockerfiles build;
finalize compose (publishes host ports 8000/8001 on the Docker host's LAN); follow
`deploy/NPM.md`.

**Done when:**
- `docker compose up --build` brings up backend + sleep-mcp, publishing 8000/8001
  on the Docker host's LAN interface.
- `npm-flip.sh` "prod" upstream is set to that Docker host's LAN IP; flipping to
  prod routes `healthbridge.example.com` to the container.
- With `Authorization: Bearer <token>`, `curl` hits `/health` and `/ingest` through
  `healthbridge.example.com`; without the token, `/ingest` returns 401.
- The DuckDB volume persists across `docker compose down/up`.

## Cross-cutting invariants (check continuously)

- Backend is the only writer; MCP opens read_only=True.
- No training/ride data anywhere.
- Source name configurable; NBSP handled.
- UTC in storage; Europe/Amsterdam for night assignment.
- All inserts idempotent.
- Topology: Cloudflare (DNS) → NPM → LAN IP:port; NPM forwards over LAN by IP:port
  (no shared Docker network); backend owns bearer-token auth.
- Every phase ends green: pytest + ruff (+ shellcheck for scripts).
