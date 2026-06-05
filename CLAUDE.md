# CLAUDE.md — HealthBridge

This file guides Claude Code when working in this repository. Read it fully before
making changes. Read `docs/SPEC.md` for the complete system specification.

## What this project is

HealthBridge pulls Apple Watch sleep + HRV + resting-HR data out of Apple HealthKit
and into a locally-hosted, queryable store, then exposes it to LLM tooling (an
existing cycling-coach MCP, and Claude) via a dedicated read-only MCP server.

**Scope is sleep-domain only.** Training/ride data lives in a separate Wahoo pipeline
and must NOT be reintroduced here. The pipeline is named broadly ("HealthBridge")
because the ingestion layer is general-purpose and may capture more HealthKit types
later, but v1 captures exactly three: sleep analysis, HRV (SDNN), and resting HR.

## Architecture (three loosely-coupled components, one canonical DB)

```
Health Auto Export ─HTTPS─▶  Ingestion Service  ──writes──▶  health.duckdb
(iOS app)        CF→NPM→LAN   (FastAPI)                          │
                                                                 │ read-only
                              Sleep MCP Server  ◀────reads───────┤
                              (FastMCP)                          │
                                                                 │ read-only
                              Cycling coach MCP (external) ◀──────┘
```

The DuckDB file is the integration boundary. The ingestion service is the ONLY
writer. Everything else reads it read-only.

## Components & directories

- `backend/`     — FastAPI ingestion service + DuckDB writes. The only writer.
- `mcp/`         — `sleep-mcp` read-only MCP server (FastMCP). Exposes sleep tools.
- `bootstrap/`   — one-shot importer that seeds history from an Apple Health export.zip.
- `deploy/`      — Dockerfiles, docker-compose (publishes host ports), NPM setup guide.
- `scripts/dev/` — local-dev launcher + NPM-flip tooling (run real services on the
  laptop, flip NPM upstreams to it, auto-revert). Mirrors the cycling-coach pattern.
- `docs/`        — SPEC.md, BUILD_ORDER.md, TESTING.md.

## Hard constraints (do not violate)

1. **Single writer.** Only `backend/` writes to DuckDB. MCP servers and the coach
   open the DB read-only (`duckdb.connect(path, read_only=True)`).
2. **No training data.** Do not add workouts, power, TSS, rides, etc. Sleep + HRV
   + RHR only. If tempted, stop — that is the Wahoo pipeline's job.
3. **Apple Watch is the sleep source.** Filter sleep samples to the Apple Watch
   source. The source name contains a NON-BREAKING SPACE (U+00A0) between "Apple"
   and "Watch" — e.g. `"Apple\u00a0Watch"`. Naive `"Apple Watch"` matching
   WILL fail. Make the source name configurable, not hardcoded. The primary client
   (Health Auto Export) exports BOTH Apple Watch and SleepWatch sleep, so the filter
   is enforced server-side in `hae_adapter.py` via `HEALTHBRIDGE_SLEEP_SOURCE`
   (NBSP-insensitive match, original string stored verbatim).
4. **UTC on the wire and in storage.** Display/tz conversion is a read-time concern.
   Storage is always UTC (TIMESTAMPTZ).
5. **Idempotent ingest.** Re-sending overlapping batches must be safe. Use natural
   primary keys + INSERT ... ON CONFLICT DO NOTHING. The phone re-sends freely.
6. **Topology: Cloudflare (DNS proxy) → router :443 → NPM container → LAN IP:port.**
   See `deploy/NETWORKING.md` (authoritative). Cloudflare is DNS/edge only — NO CF
   Access, NO Tunnel. NPM is the flippable reverse proxy; it does NOT share a Docker
   network with the apps (multiple Docker hosts), so it forwards over the LAN by
   `IP:port` — laptop in dev, a Docker host in prod. App containers PUBLISH ports
   8000/8001 on their host's LAN. The backend owns auth via a bearer token
   (`auth.py`, constant-time compare); the phone sends `Authorization: Bearer
   <HEALTHBRIDGE_TOKEN>`. `HEALTHBRIDGE_DEV=1` bypasses auth ONLY for the
   localhost-direct `--no-flip` path; the normal CF→NPM→laptop dev path exercises
   real auth.
7. **Strict TDD — non-negotiable.** Follow `docs/TESTING.md`. Every unit of real
   logic is pinned by a test written for it; every resolved `TODO(claude-code)`
   ships with a test; `pytest` + `ruff` must be green before a phase is "done".
   Write the failing test first, then implement. Idempotency tests are mandatory
   for all write paths. Do not advance build phases on red.
8. **Stay on agreed decisions.** Python/FastAPI backend, DuckDB storage, NPM +
   bearer-token auth, name "HealthBridge". Do not propose swapping these.

## Domain knowledge (learned during data exploration — trust this)

- Apple Watch writes stage-level sleep: `AsleepCore`, `AsleepDeep`, `AsleepREM`,
  `AsleepUnspecified`, `Awake`, `InBed` (HealthKit value strings are prefixed with
  `HKCategoryValueSleepAnalysis` — strip that prefix on ingest).
- SleepWatch (a third-party app) also writes sleep but only two-state
  (Asleep/InBed) and is noisier. We deliberately ignore it. Apple Watch only.
- A "night" is defined noon-to-noon: a sample whose start is before 12:00 belongs
  to that calendar date's night; at/after 12:00 it belongs to the next day's night.
- Sleep efficiency = asleep_seconds / (wake_time - bed_time) within the night.
- Sleep is event-driven: the Watch emits a new segment on each stage change, so a
  night has ~15–50 segments (median ~8 min, range ~1–75 min). This is the native
  resolution — there is no sampling-rate control; capture it all (HAE "summarize off").
- HRV (SDNN) is a periodic BACKGROUND measurement, not sleep-only: ~6 readings/DAY
  at a steady ~4 h interval, around the clock (measured from real data — NOT
  "5–15 per night" as earlier assumed). That means a noon-to-noon night usually
  contains only ~2–3 HRV samples, so `nightly_summary.hrv_avg_ms` is averaged over a
  small set. For a stable recovery signal use a multi-night rolling baseline, not one
  night's average. Samples before noon belong to that night.
- Resting HR: Apple writes one value per day (a daily computed value). Key it by date.
- The Apple Watch is conservative about classifying stationary periods (e.g. car
  rides) as sleep — it does NOT need extra false-positive filtering for our purpose.
  (SleepWatch does over-classify, another reason we ignore it.)

## Build order (follow this sequence)

1. **backend/** — schema + ingest endpoint + nightly-summary recompute. Run locally.
2. **bootstrap/** — import an existing `export.zip` to seed history; validates schema
   against 8 years of real data before the phone ever runs.
3. **scripts/dev/** — NPM-flip local-dev/e2e tooling. This is the PRIMARY dev path:
   run backend+MCP in a dedicated venv on the laptop, flip NPM's upstream to the
   laptop's LAN IP, and exercise the full CF→NPM→laptop path (real bearer auth,
   real public hostname, real client). Auto-reverts on exit.
4. **deploy/** — only after laptop e2e is green: compose publishes host ports on a
   Docker host; flip NPM's upstream to that host's LAN IP. Same flip, different LAN IP.
5. **Health Auto Export** — configure the HAE app to POST to `/ingest/hae`
   (see `docs/HAE_SETUP.md`); validate end-to-end through the flipped dev path.
6. **mcp/** — start with `get_latest_night` + `get_nightly_range`; expand to recovery
   signals.

## Conventions

- Python 3.12+, type hints everywhere, Pydantic v2 for all wire models.
- Format with `ruff format`, lint with `ruff check`. Config in `pyproject.toml`.
- Tests with `pytest`. Every ingest path needs an idempotency test (insert twice,
  assert no duplicates).
- Keep the DuckDB schema in ONE place: `backend/healthbridge/schema.sql`. Both the
  backend and the MCP server read column names from there conceptually; don't
  duplicate DDL.
- Secrets via environment variables, never committed. See `.env.example`.
- Commit messages: conventional commits (feat:, fix:, chore:, docs:).

## What "done" looks like for v1

- `curl` POST of a sample payload to the local backend writes rows and is idempotent.
- `bootstrap` ingests a real export.zip and populates nightly_summary for all nights.
- Health Auto Export POSTs to `/ingest/hae` with a bearer token; rows appear and
  nightly summaries recompute (re-send is idempotent).
- `sleep-mcp` returns a correct `get_latest_night()` and `get_nightly_range()`.
- Laptop e2e green via CF→NPM→laptop (real bearer auth). Prod is the same path
  with NPM's upstream flipped to a Docker host's LAN IP. `/ingest` requires the token.
