# Health Auto Export (HAE) — Setup

HealthBridge's primary ingestion path is the **Health Auto Export** iOS app
(https://github.com/Lybron/health-auto-export), which reads Apple HealthKit and
POSTs JSON to a REST endpoint. The backend exposes `POST /ingest/hae`, which
normalizes HAE's format to the canonical `IngestData` and reuses the same write
path as `/ingest` (see `backend/healthbridge/hae_adapter.py`).

## Tier

- **Basic** (one-time purchase): manual "Quick Export" — enough for dev/testing.
- **Premium** (subscription/lifetime): automatic scheduled background export —
  needed for hands-off prod sync.
- Free (widgets only) is not sufficient.

## HealthKit permissions (do this first)

HAE only lists metrics it has read permission for. In iOS:
**Settings → Health → Data Access & Devices → Health Auto Export** → enable read for
**Sleep**, **Heart Rate Variability**, **Resting Heart Rate**. (If a metric is
missing from HAE's export picker, its permission is off here.)

## Auto Export configuration

In the HAE app's Auto Export screen:

1. **Metrics — enable ONLY these three** (turn everything else off):
   - Resting Heart Rate
   - Heart Rate Variability
   - Sleep
   Leave raw **Heart Rate** OFF (it's min/avg/max, ~11k samples/30d — not what we
   use and it bloats payloads). Leave **Sleep Changes** OFF (that's a symptom log,
   not stage data). Limiting metrics is also a privacy measure: only data we ingest
   leaves the phone.
2. **Aggregation: summarize data OFF.** This is required — it makes Sleep export as
   per-stage segments (which map to `sleep_samples`) instead of pre-aggregated
   nightly totals.
3. **Time grouping: minutes.** Seconds gives no extra precision (segment/sample
   boundaries are already second-accurate); hours would re-aggregate. Minutes is the
   right setting.
4. **Destination: REST API**
   - URL: `https://healthbridge.example.com/ingest/hae`
   - Method: POST, Format: JSON
   - Headers: tap **Add Headers** and fill the two fields separately —
     **Key** = `Authorization`, **Value** = `Bearer <HEALTHBRIDGE_TOKEN>`
     (the word `Bearer`, one space, then the token — all in the Value field).
     Do NOT put `Bearer` in the Key field; the Key is just `Authorization`.
     `Content-Type` is added automatically.

     | Field | Value |
     |-------|-------|
     | Key   | `Authorization` |
     | Value | `Bearer <HEALTHBRIDGE_TOKEN>` |
5. Dev: trigger a **manual** export. Prod: enable the scheduled/automatic export
   (Premium).

## What the adapter does (so you don't have to pre-process)

- **Source filtering.** HealthKit's per-type sleep export includes BOTH
  `Apple Watch` AND `SleepWatch`. The adapter keeps only the configured Apple Watch
  source (`HEALTHBRIDGE_SLEEP_SOURCE`) and drops SleepWatch. Matching is
  NBSP-insensitive (the real source name has a U+00A0 between "Apple" and "Watch"),
  so you can set the env value with a normal space; the original string is stored
  verbatim.
- **Stage mapping.** HAE's short stage names map to the canonical backend strings:
  Core→AsleepCore, Deep→AsleepDeep, REM→AsleepREM, Asleep→AsleepUnspecified,
  Awake→Awake, In Bed→InBed.
- **Timestamps.** HAE sends local wall-clock with offset (`2026-05-06 01:09:08
  +0200`). Sleep/HRV are converted to UTC. RHR is keyed by the LOCAL calendar date
  (its timestamp is local midnight; converting to UTC first would shift the day).
- **Idempotency.** Re-sending overlapping windows is safe (natural-key
  `ON CONFLICT DO NOTHING`), so HAE can re-export freely.
- **Out-of-scope metrics** (steps, vitals, symptoms, …) are ignored, so an all-on
  export won't break ingest — but limiting metrics in-app is still recommended.

## Backend config

Set the Apple Watch source name so the adapter filters correctly:

```
HEALTHBRIDGE_SLEEP_SOURCE=Apple Watch   # normal space is fine (NBSP-insensitive)
```

Unset/empty = keep all sources (no SleepWatch filtering) — not recommended.

## Verify end-to-end (dev)

With NPM flipped to the laptop and uvicorn running:

```bash
curl -sS -X POST https://healthbridge.example.com/ingest/hae \
  -H "Authorization: Bearer $HEALTHBRIDGE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data @"HealthAutoExport-XXXX.json" | jq
```

Expect `sleep_written` to equal the Apple-Watch row count (SleepWatch excluded),
plus `hrv_written` / `rhr_written` and `nights_recomputed`. Re-POST the same file →
all `*_written` are 0 (idempotent).
