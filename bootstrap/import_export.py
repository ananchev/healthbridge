"""Seed HealthBridge history from an existing Apple Health export.zip.

Reuses the backend's db.py write functions — NO parallel write logic. This parses
the export XML into the same Pydantic models the wire format uses, then calls the
same insert + recompute paths.

Usage:
    python -m bootstrap.import_export path/to/export.zip [--db /data/health.duckdb]

The XML parsing here is adapted from validated exploration scripts. Key gotchas
already handled:
  - Apple Watch source name contains a non-breaking space (U+00A0).
  - Sleep stage values are prefixed HKCategoryValueSleepAnalysis (stripped).
  - HRV type is HeartRateVariabilitySDNN; RHR is RestingHeartRate.
  - Streaming parse (iterparse + elem.clear()) to handle multi-GB exports.

NOTE for Claude Code: the parsing is implemented (it's validated logic). The write
calls reference db.py functions that you implement on the backend side. Keep the
SOURCE filter configurable via --sleep-source.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

# Backend package must be importable (monorepo: add backend/ to path or install -e).
from healthbridge import db
from healthbridge.models import HrvSample, RhrSample, SleepSample

DEFAULT_SLEEP_SOURCE = "Apple\u00a0Watch van Owner"  # NBSP between Apple and Watch

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
RHR_TYPE = "HKQuantityTypeIdentifierRestingHeartRate"

XML_INNER_PATH = "apple_health_export/export.xml"


def parse_apple_dt(s: str) -> datetime:
    # Apple format: '2026-05-20 06:47:46 +0200'
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


def parse_export(zip_path: str, sleep_source: str):
    """Stream-parse the export, yielding typed samples.

    Returns (sleep, hrv, rhr) lists of Pydantic models.
    """
    sleep: list[SleepSample] = []
    hrv: list[HrvSample] = []
    rhr: list[RhrSample] = []

    with zipfile.ZipFile(zip_path) as z:
        with z.open(XML_INNER_PATH) as f:
            for _event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag != "Record":
                    continue
                rtype = elem.get("type", "")

                if rtype == SLEEP_TYPE and elem.get("sourceName") == sleep_source:
                    sleep.append(
                        SleepSample(
                            start=parse_apple_dt(elem.get("startDate")),
                            end=parse_apple_dt(elem.get("endDate")),
                            stage=elem.get("value").replace(
                                "HKCategoryValueSleepAnalysis", ""
                            ),
                            source=elem.get("sourceName"),
                            source_version=elem.get("sourceVersion"),
                        )
                    )
                elif rtype == HRV_TYPE:
                    hrv.append(
                        HrvSample(
                            timestamp=parse_apple_dt(elem.get("startDate")),
                            value_ms=float(elem.get("value")),
                            source=elem.get("sourceName"),
                        )
                    )
                elif rtype == RHR_TYPE:
                    rhr.append(
                        RhrSample(
                            date=parse_apple_dt(elem.get("startDate")).date(),
                            value_bpm=float(elem.get("value")),
                            source=elem.get("sourceName"),
                        )
                    )

                elem.clear()

    return sleep, hrv, rhr


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed HealthBridge from Apple Health export.zip")
    ap.add_argument("zip_path", help="Path to export.zip")
    ap.add_argument("--db", default="/data/health.duckdb", help="DuckDB path")
    ap.add_argument("--sleep-source", default=DEFAULT_SLEEP_SOURCE,
                    help="Apple Watch source name (note the non-breaking space)")
    args = ap.parse_args(argv)

    print(f"Parsing {args.zip_path} ...")
    sleep, hrv, rhr = parse_export(args.zip_path, args.sleep_source)
    print(f"Parsed: {len(sleep)} sleep, {len(hrv)} hrv, {len(rhr)} rhr samples.")

    conn = db.connect(args.db)
    db.init_schema(conn)

    # Reuse backend write paths — single source of write logic.
    n_sleep = db.insert_sleep(conn, sleep)
    n_hrv = db.insert_hrv(conn, hrv)
    n_rhr = db.insert_rhr(conn, rhr)

    nights = db.affected_nights_from_sleep(sleep)
    recomputed = db.recompute_nights(conn, nights)

    print(f"Wrote: {n_sleep} sleep, {n_hrv} hrv, {n_rhr} rhr (new rows).")
    print(f"Recomputed {len(recomputed)} nightly summaries.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
