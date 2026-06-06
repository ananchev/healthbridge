# HealthBridge — System Specification v1.0

## 1. Purpose

Pull Apple Watch sleep + HRV + resting-HR data from Apple HealthKit into a
locally-hosted DuckDB store, exposed to LLM tooling (an existing cycling-coach MCP
and Claude) via a dedicated read-only MCP server.

**In scope:** sleep analysis, heart-rate variability (SDNN), resting heart rate.
**Out of scope:** all training/ride data (lives in a separate Wahoo pipeline),
workouts, power, steps, weight, blood oxygen. v1 is sleep-domain only.

## 2. Architecture

```
┌─────────────────────┐   HTTPS    ┌──────────────────────────┐   reads   ┌──────────────────────────┐
│  Health Auto Export │ CF→NPM→LAN │  Ingestion Service       │ ◀──────── │  Sleep MCP Server        │
│  (iOS app)          │ ─────────▶ │  (NPM → FastAPI)         │ read-only │  (FastMCP)               │
│                     │  bearer tok │                          │           │                          │
│  • Sleep/HRV/RHR    │            │  • Validate (Pydantic)   │           │  • get_latest_night      │
│  • REST API export  │            │  • Dedupe (ON CONFLICT)  │           │  • get_nightly_range     │
│  • Manual + sched   │            │  • Normalize (HAE→canon) │           │  • get_recovery_status   │
└─────────────────────┘            │  • Write DuckDB          │           │  • get_hrv_trend         │
                                   │  • Recompute summaries   │           └──────────────────────────┘
                                   └──────────────────────────┘
                                                │                                       ▲
                                                ▼                                       │
                                         ┌──────────────┐                               │
                                         │ health.duckdb│ ──────────────────────────────┘
                                         │ (volume)     │   read-only
                                         └──────────────┘
                                                ▲
                                                │ read-only (later)
                                         ┌──────────────┐
                                         │ cycling coach│  (external, existing)
                                         │ MCP          │
                                         └──────────────┘
```

The DuckDB file is the integration boundary. The ingestion service is the sole
writer. All other consumers open it read-only.

## 3. Components

### 3.1 Ingestion Client (Health Auto Export)

The client is the third-party **Health Auto Export (HAE)** iOS app
(https://github.com/Lybron/health-auto-export). It reads HealthKit and POSTs JSON to
a REST endpoint — no custom app to build/sign. See `docs/HAE_SETUP.md` for the exact
configuration.

**HealthKit metrics:** Sleep, Heart Rate Variability (SDNN), Resting Heart Rate.

**Export config:** summarize OFF (per-stage sleep segments), time grouping minutes,
destination REST API → `https://healthbridge.example.com/ingest/hae`, header
`Authorization: Bearer <HEALTHBRIDGE_TOKEN>`. Manual export in dev; scheduled
(HAE Premium) in prod.

**Trigger modes:** manual (Quick Export) and scheduled background export.

**Normalization & source filtering** happen server-side in
`backend/healthbridge/hae_adapter.py` (HAE exports both Apple Watch and SleepWatch):
filter to the configured Apple Watch source (`HEALTHBRIDGE_SLEEP_SOURCE`,
NBSP-insensitive), map short stage names to canonical, convert sleep/HRV to UTC, key
RHR by local date. Idempotent re-export is safe.

### 3.2 Ingestion Service

**Stack:** Python 3.12, FastAPI, Pydantic v2, DuckDB. Docker, behind NPM.

**Endpoints:**
- `POST /ingest` — receive payload, validate, dedupe-write, recompute affected
  nightly summaries. Returns counts written + per-type latest timestamps.
- `GET /health` — liveness.
- `GET /stats` — per-type counts, latest timestamps, freshness. Debug aid.

**Auth:** Cloudflare is DNS-only (no CF Access). The backend owns auth
via a bearer token (`auth.py`): the phone sends `Authorization: Bearer
<HEALTHBRIDGE_TOKEN>`, compared constant-time against the env var. `/health` is
unauthenticated (liveness); `/ingest` and `/stats` require the token.
`HEALTHBRIDGE_DEV=1` bypasses for localhost. Optionally gate `/stats` further with
an NPM Access List for browser use.

### 3.3 Sleep MCP Server ("sleep-mcp")

**Stack:** Python, FastMCP. Opens DuckDB **read-only**.

**Auth:** OAuth 2.1 Resource Server (claude.ai web connector). Tokens are issued by a
shared Authorization Server (`mcp-auth`, separate repo) and verified here (HS256,
same signing key). One AS, many Resource Servers. Full model, env vars, and the
browser-path validation checklist: **`docs/MCP_AUTH.md`**.

**Tools:**
- `get_latest_night()` — most recent night's summary.
- `get_nightly_summary(date)` — one night.
- `get_nightly_range(start_date, end_date)` — table of nights.
- `get_hrv_trend(days=30)` — HRV series + rolling baseline.
- `get_sleep_debt(window_days=7, target_hours=8)` — accumulated deficit.
- `get_recovery_status()` — composite green/yellow/red from HRV vs baseline,
  RHR delta, and recent sleep debt, with human-readable reasoning.

## 4. Data model (DuckDB)

See `backend/healthbridge/schema.sql` for authoritative DDL. Summary:

- `sleep_samples(start_ts, end_ts, stage, source, source_version, ingested_at)`
  PK `(start_ts, end_ts, stage, source)`.
- `hrv_samples(ts, value_ms, source, ingested_at)` PK `(ts, source)`.
- `rhr_samples(date, value_bpm, source, ingested_at)` PK `(date, source)`.
- `nightly_summary(night_date PK, bed_time, wake_time, time_in_bed_seconds,
  asleep_seconds, rem_seconds, deep_seconds, core_seconds, awake_seconds,
  efficiency_pct, hrv_avg_ms, rhr_bpm, computed_at)`.

Raw tables are append-only and idempotent. `nightly_summary` is derived; recompute
affected nights on each ingest (drop+reinsert that night's row, pure SQL).

## 5. Wire format

`POST /ingest` body — all timestamps UTC ISO-8601:

```json
{
  "device_id": "example-iphone",
  "synced_at": "2026-05-20T22:14:00Z",
  "data": {
    "sleep": [
      {"start": "2026-05-19T22:47:44Z", "end": "2026-05-19T23:04:46Z",
       "stage": "AsleepCore", "source": "Apple Watch",
       "source_version": "11.4"}
    ],
    "hrv": [
      {"timestamp": "2026-05-19T23:30:00Z", "value_ms": 34.2,
       "source": "Apple Watch"}
    ],
    "rhr": [
      {"date": "2026-05-20", "value_bpm": 55, "source": "Apple Watch"}
    ]
  }
}
```

`stage` arrives WITHOUT the `HKCategoryValueSleepAnalysis` prefix (the app strips
it). Backend stores it as-is.

### 5b. Health Auto Export variant — `POST /ingest/hae`

The PRIMARY ingestion path is the Health Auto Export (HAE) iOS app, which POSTs a
different shape. `/ingest/hae` accepts it and normalizes to the same `IngestData`,
reusing the `/ingest` write path. See `docs/HAE_SETUP.md` and
`backend/healthbridge/hae_adapter.py`.

HAE body (relevant subset; timestamps are local wall-clock with offset):

```json
{"data": {"metrics": [
  {"name": "sleep_analysis", "units": "hr", "data": [
    {"start": "2026-05-29 01:09:08 +0200", "end": "2026-05-29 01:11:08 +0200",
     "value": "Core", "source": "Apple Watch"}]},
  {"name": "heart_rate_variability", "units": "ms", "data": [
    {"date": "2026-05-29 00:08:57 +0200", "qty": 31.5,
     "source": "Apple Watch"}]},
  {"name": "resting_heart_rate", "units": "count/min", "data": [
    {"date": "2026-05-29 00:00:38 +0200", "qty": 57,
     "source": "Apple Watch"}]}
]}}
```

Adapter rules: filter to the configured Apple Watch source (drops SleepWatch;
NBSP-insensitive); map short stages (Core/Deep/REM/Asleep/Awake →
AsleepCore/AsleepDeep/AsleepREM/AsleepUnspecified/Awake); convert sleep/HRV to UTC;
key RHR by LOCAL date (its timestamp is local midnight). Unknown metrics ignored.

## 6. "Night" definition

A sample with local start time < 12:00 belongs to that date's night; >= 12:00
belongs to the next day's night. v1 assumes Europe/Amsterdam throughout; proper
travel-aware tz handling is deferred.

## 7. Derived metrics

- `efficiency_pct = asleep_seconds / time_in_bed_seconds * 100`
- `hrv_avg_ms` = mean of HRV samples assigned to that night.
- Recovery status: compare night HRV to 30-day rolling baseline; RHR to baseline;
  sleep debt over 7 days. Green/yellow/red with reasoning. Thresholds tunable.

## 8. Bootstrap

`python -m bootstrap.import_export path/to/export.zip` ingests an existing Apple
Health XML export through the same dedupe paths, seeding full history. Must be
idempotent and reuse the backend's write functions (no parallel logic).

## 9. Deployment

Topology (see `deploy/NETWORKING.md`): Internet → Cloudflare (DNS proxy) → router
:443 → NPM container → `<LAN-IP>:port`. NPM does NOT share a Docker network with
the apps; it forwards over the LAN by IP:port (multiple Docker hosts).

- `docker compose up` brings up `backend` and `sleep-mcp`, **publishing** ports
  8000/8001 on the Docker host's LAN interface.
- `health.duckdb` on a named volume, mounted RW in backend, RO in sleep-mcp.
- NPM proxies `healthbridge.example.com → <docker-host-LAN-IP>:8000` and
  `mcp-sleep.example.com → <docker-host-LAN-IP>:8001`.
- The ingestion client (HAE) always targets the stable public hostname; only NPM's
  upstream IP differs between dev (laptop) and prod (Docker host).
- Auth: backend bearer token (phone). Optional NPM Access List on `/stats`.
- See `deploy/NPM.md`.

## 9b. Local development

`scripts/dev/start-dev-stack.sh` runs the real backend + MCP on the laptop and
flips the NPM upstreams to point at it (via `npm-flip.sh`, NPM API), so the public
hostnames and the client's real endpoint hit local code. Full-state rollback,
auto-revert on exit, host-side watchdog fallback, and an `APP_ENV` banner mark the
flipped session. Mirrors the cycling-coach dev pattern. See `scripts/dev/README.md`.

## 10. Non-goals / explicit deferrals

- Travel-aware time zones (assume Europe/Amsterdam).
- Any training data (Wahoo pipeline owns it).
- Real-time push (ingestion is manual/scheduled export, not streaming).
- A web dashboard (the MCP + existing tooling cover consumption for now).
