# HealthBridge

Pull Apple Watch **sleep + HRV + resting-HR** from Apple HealthKit into a
locally-hosted DuckDB store, exposed to LLM tooling (cycling-coach MCP, Claude) via
a dedicated read-only MCP server.

**Sleep-domain only.** Training/ride data lives in a separate Wahoo pipeline and is
intentionally excluded here.

## Repo layout

```
backend/     FastAPI ingestion service + DuckDB writes (the ONLY writer)
mcp/         sleep-mcp — read-only MCP server (FastMCP)
bootstrap/   one-shot importer: seed history from an Apple Health export.zip
ios/         SwiftUI helper app (HealthBridge) — reads HealthKit, POSTs to backend
deploy/      Dockerfiles, docker-compose (NPM network), NPM setup guide
scripts/dev/ Local-dev launcher + NPM-flip tooling (run locally, flip NPM)
docs/        SPEC.md (full specification)
CLAUDE.md    guidance for Claude Code — READ THIS FIRST
```

## Start here

1. Read `CLAUDE.md` (root) and `docs/SPEC.md`.
2. Follow the build order in `CLAUDE.md` / `docs/BUILD_ORDER.md`:
   backend → bootstrap → deploy → ios → mcp.

## Quick local dev (backend)

```bash
cd backend
pip install -e ".[dev]"
HEALTHBRIDGE_DEV=1 HEALTHBRIDGE_DB=./dev.duckdb uvicorn healthbridge.app:app --reload
# then in another shell:
curl localhost:8000/health
```

## Seed history from an existing export

```bash
cd backend && pip install -e .
cd ..
python -m bootstrap.import_export ~/Downloads/export.zip --db ./dev.duckdb \
  --sleep-source "Apple Watch van Owner"   # mind the non-breaking space
```

## Local development (NPM-flip)

Run the real backend + MCP on your laptop and flip the production NPM upstreams to
point at it — test through the real hostnames without deploying. Auto-reverts on exit.

```bash
cp .env.dev.example .env.dev    # fill in NPM details + proxy IDs
./scripts/dev/start-dev-stack.sh            # flip + run; Ctrl-C reverts
./scripts/dev/start-dev-stack.sh --no-flip  # localhost only, no NPM changes
```

See `scripts/dev/README.md`.

## Deploy

```bash
cd deploy
cp ../.env.example .env   # fill in HEALTHBRIDGE_TOKEN, BACKEND_PORT, etc.
docker compose up --build
```

Topology: Cloudflare (DNS proxy) → router :443 → NPM container → `<LAN-IP>:port`.
NPM forwards over the LAN by IP:port (no shared Docker network). Dev flips the
upstream to the laptop; prod flips it to a Docker host. The backend authenticates
the phone with a bearer token. See `deploy/NETWORKING.md` and `deploy/NPM.md`.

## Testing

Strict TDD — see `docs/TESTING.md`. Every unit of logic is pinned by a test;
`pytest` + `ruff` green before any build phase is done; idempotency tests are
mandatory on write paths.

```bash
cd backend && pip install -e ".[dev]" && pytest && ruff check
```

## Key gotchas (learned the hard way)

- The Apple Watch HealthKit source name contains a **non-breaking space** (U+00A0)
  between "Apple" and "Watch". Plain-space matching fails. Source name is
  configurable everywhere.
- A "night" is **noon-to-noon**. Sample start < 12:00 local → that date's night.
- Apple Watch sleep is **stage-level** (REM/Core/Deep/Awake). SleepWatch is
  two-state and noisier — we ignore it.
- Ingest is **idempotent**; the phone re-sends overlapping batches freely.
