-- HealthBridge DuckDB schema — authoritative DDL.
-- The ingestion service is the ONLY writer. MCP servers open read-only.
-- All timestamps are UTC (TIMESTAMPTZ). Idempotent via natural primary keys.

-- ---------------------------------------------------------------------------
-- Raw samples (append-only, source of truth)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sleep_samples (
    start_ts        TIMESTAMPTZ NOT NULL,
    end_ts          TIMESTAMPTZ NOT NULL,
    stage           VARCHAR     NOT NULL,  -- AsleepCore|AsleepDeep|AsleepREM|AsleepUnspecified|Awake|InBed
    source          VARCHAR     NOT NULL,
    source_version  VARCHAR,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (start_ts, end_ts, stage, source)
);

CREATE TABLE IF NOT EXISTS hrv_samples (
    ts              TIMESTAMPTZ NOT NULL,
    value_ms        DOUBLE      NOT NULL,
    source          VARCHAR     NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ts, source)
);

CREATE TABLE IF NOT EXISTS rhr_samples (
    date            DATE        NOT NULL,
    value_bpm       DOUBLE      NOT NULL,
    source          VARCHAR     NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (date, source)
);

-- ---------------------------------------------------------------------------
-- Derived nightly summary (recomputed on each ingest for affected nights)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nightly_summary (
    night_date           DATE        NOT NULL PRIMARY KEY,
    bed_time             TIMESTAMPTZ,
    wake_time            TIMESTAMPTZ,
    time_in_bed_seconds  INTEGER,
    asleep_seconds       INTEGER,
    rem_seconds          INTEGER,
    deep_seconds         INTEGER,
    core_seconds         INTEGER,
    awake_seconds        INTEGER,
    efficiency_pct       DOUBLE,
    hrv_avg_ms           DOUBLE,
    rhr_bpm              DOUBLE,
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Helpful indexes for range queries from the MCP layer
CREATE INDEX IF NOT EXISTS idx_sleep_start ON sleep_samples (start_ts);
CREATE INDEX IF NOT EXISTS idx_hrv_ts      ON hrv_samples (ts);
