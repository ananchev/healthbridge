# HealthBridge

HealthBridge pulls **Apple Watch sleep, heart-rate variability (HRV), and resting
heart rate** out of Apple HealthKit into a private, locally-hosted store, derives
nightly sleep and recovery metrics, and exposes them to LLM tools (Claude, a
cycling-coach MCP) through a dedicated **read-only MCP server**.

Single-user and self-hosted. **Sleep-domain only** — training/ride data lives in a
separate pipeline and is intentionally excluded.

## What it gives you

- **A queryable history of your sleep & recovery** — every night's stages,
  efficiency, HRV and resting HR, kept locally in DuckDB.
- **Recovery answers for an LLM coach** — Claude (or the cycling-coach MCP) can ask
  "how did I sleep?", "what's my HRV trend?", "am I recovered?" and get structured,
  reasoned answers.
- **Hands-off capture** — the Health Auto Export iOS app syncs HealthKit to the
  backend on a schedule; re-sends are safe (ingest is idempotent).

## How it works

```
Apple Watch → HealthKit
      │  Health Auto Export (iOS app, scheduled)
      ▼  HTTPS  POST /ingest/hae   (bearer token)
Ingestion service (FastAPI) ──writes──▶ health.duckdb  (UTC, idempotent)
                                              │ read-only
   Claude (web) ──OAuth──▶ sleep-mcp (MCP) ◀──┤
                             ▲                 │ read-only
   mcp-auth (shared OAuth AS, separate repo)   └──◀ cycling-coach MCP
```

- The DuckDB file is the integration boundary: **the ingestion service is the only
  writer**; everything else opens it read-only.
- Storage is UTC; a "night" is **noon-to-noon** (Europe/Amsterdam). Times are
  localized for display at read time.

## What Claude can ask (MCP tools)

| Tool | Returns |
|---|---|
| `get_latest_night` | most recent night — stages, efficiency, HRV, RHR (local times + raw seconds) |
| `get_nightly_summary(date)` | one night by date |
| `get_nightly_range(start, end)` | nights in a date range |
| `get_hrv_trend(days)` | HRV series + rolling baseline |
| `get_sleep_debt(window, target)` | cumulative shortfall **and** net debt vs target |
| `get_recovery_status` | green / yellow / red from HRV-vs-baseline, RHR-vs-baseline, and 7-night sleep debt, with reasons |

## What's captured

- **Sleep** — Apple Watch stage-level segments (Core/Deep/REM/Awake), rolled up into
  nightly efficiency and per-stage durations.
- **HRV (SDNN)** — Apple's periodic background readings (~6/day).
- **Resting HR** — one value per day.

Apple Watch is the source; the noisier two-state SleepWatch data is filtered out.

## Access & auth

- **Ingestion** (`/ingest`, `/ingest/hae`) — bearer token; the iOS app sends it on
  every request.
- **MCP** (`sleep-mcp`) — OAuth 2.1: Claude authenticates against the shared
  `mcp-auth` authorization server, and sleep-mcp verifies the issued token.

## Components

| Path | Role |
|---|---|
| `backend/` | FastAPI ingestion + DuckDB writes — the **only** writer |
| `mcp/` | `sleep-mcp` — read-only MCP server (OAuth Resource Server) |
| `bootstrap/` | one-shot importer to seed history from an Apple Health `export.zip` |
| `deploy/` | Dockerfiles, compose, networking/NPM guide |
| `scripts/dev/` | local-run + NPM-flip development tooling |
| `docs/` | full specification and setup guides |
| `mcp-auth` *(separate repo)* | shared OAuth 2.1 Authorization Server for the MCP connectors |

## Documentation

- **`docs/SPEC.md`** — full system specification.
- **`docs/HAE_SETUP.md`** — configure the Health Auto Export iOS app.
- **`docs/MCP_AUTH.md`** — the OAuth model (shared AS + resource servers), env, validation.
- **`deploy/NETWORKING.md`** — topology, NPM, and deployment.
- **`docs/BUILD_ORDER.md`** and **`scripts/dev/README.md`** — development & local-test workflow.
- **`docs/TESTING.md`** — testing discipline.
- **`CLAUDE.md`** — guidance for Claude Code working in this repo.
