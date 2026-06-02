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
│  iOS Helper App     │ CF→NPM→LAN │  Ingestion Service       │ ◀──────── │  Sleep MCP Server        │
│  (HealthBridge)     │ ─────────▶ │  (NPM → FastAPI)         │ read-only │  (FastMCP)               │
│                     │  bearer tok │                          │           │                          │
│  • HKObserverQuery  │            │  • Validate (Pydantic)   │           │  • get_latest_night      │
│  • Sync cursors     │            │  • Dedupe (ON CONFLICT)  │           │  • get_nightly_range     │
│  • Manual+sched+obs │            │  • Write DuckDB          │           │  • get_recovery_status   │
└─────────────────────┘            │  • Recompute summaries   │           │  • get_hrv_trend         │
                                   └──────────────────────────┘           └──────────────────────────┘
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

### 3.1 iOS Helper App ("HealthBridge")

**Stack:** SwiftUI + HealthKit, no third-party dependencies.

**HealthKit read permissions:**
- `HKCategoryTypeIdentifierSleepAnalysis`
- `HKQuantityTypeIdentifierHeartRateVariabilitySDNN`
- `HKQuantityTypeIdentifierRestingHeartRate`

**UI (single screen):**
- Endpoint URL (e.g. `https://healthbridge.example.com/ingest`)
- Bearer token (stored in Keychain), sent as Authorization: Bearer <token>
- Per-type status: last sync time, sample count, last error
- "Sync now" button
- "Auto-sync on new samples" toggle (enables observers)

**Sync cursors:** per data type, persist the max `startDate` successfully sent.
Next sync queries only samples newer than the cursor. Advance only on HTTP 2xx.

**Three trigger modes (coexist):**
1. Manual — "Sync now" button.
2. Scheduled — Background App Refresh, ~daily.
3. Observer — `HKObserverQuery` + `enableBackgroundDelivery` per type.

**Source filtering:** sleep samples filtered to the Apple Watch source. The source
name contains a non-breaking space (U+00A0). Make it configurable in the app
(default to the known value, allow override).

**Build/deploy:** Xcode, free Apple Dev account, weekly cert refresh via reinstall.
Bundle ID `cc.tonio.healthbridge`.

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
  "device_id": "example-iphone-16pm",
  "synced_at": "2026-05-20T22:14:00Z",
  "data": {
    "sleep": [
      {"start": "2026-05-19T22:47:44Z", "end": "2026-05-19T23:04:46Z",
       "stage": "AsleepCore", "source": "Apple Watch van Owner",
       "source_version": "11.4"}
    ],
    "hrv": [
      {"timestamp": "2026-05-19T23:30:00Z", "value_ms": 34.2,
       "source": "Apple Watch van Owner"}
    ],
    "rhr": [
      {"date": "2026-05-20", "value_bpm": 55, "source": "Apple Watch van Owner"}
    ]
  }
}
```

`stage` arrives WITHOUT the `HKCategoryValueSleepAnalysis` prefix (the app strips
it). Backend stores it as-is.

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
- The iOS app always targets the stable public hostname; only NPM's upstream IP
  differs between dev (laptop) and prod (Docker host).
- Auth: backend bearer token (phone). Optional NPM Access List on `/stats`.
- See `deploy/NPM.md`.

## 9b. Local development

`scripts/dev/start-dev-stack.sh` runs the real backend + MCP on the laptop and
flips the NPM upstreams to point at it (via `npm-flip.sh`, NPM API), so the public
hostnames and the iOS app's real endpoint hit local code. Full-state rollback,
auto-revert on exit, host-side watchdog fallback, and an `APP_ENV` banner mark the
flipped session. Mirrors the cycling-coach dev pattern. See `scripts/dev/README.md`.

## 10. Non-goals / explicit deferrals

- Travel-aware time zones (assume Europe/Amsterdam).
- Any training data (Wahoo pipeline owns it).
- Real-time push beyond what HKObserverQuery throttling allows.
- A web dashboard (the MCP + existing tooling cover consumption for now).
