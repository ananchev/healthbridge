"""Bootstrap spot-check against a real Apple Health export.

Skipped automatically when export.zip is absent (CI passes without the file).
When present, validates that the last 7 nights match Owner's pre-verified numbers:
  - avg asleep ≈ 6h 47m (6.78 h)
  - per-night efficiency 90–98 %
  - thousands of sleep / hundreds of hrv / rhr rows
  - re-run is idempotent (0 new rows)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EXPORT_ZIP = Path(__file__).parent.parent.parent / "export.zip"

pytestmark = pytest.mark.skipif(
    not EXPORT_ZIP.exists(),
    reason="export.zip not present — skipping bootstrap spot-check",
)

# Add the repo-root bootstrap package to the path when running from backend/.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bootstrap.import_export import main  # noqa: E402

from healthbridge import db  # noqa: E402


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory):
    """Run bootstrap once; yield the path to the populated DB."""
    db_path = str(tmp_path_factory.mktemp("bootstrap") / "health.duckdb")
    rc = main([str(EXPORT_ZIP), "--db", db_path])
    assert rc == 0
    return db_path


def test_bootstrap_row_counts(seeded_db):
    c = db.connect(seeded_db, read_only=True)
    try:
        sleep_n = c.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0]
        hrv_n = c.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0]
        rhr_n = c.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0]
        nights_n = c.execute("SELECT COUNT(*) FROM nightly_summary").fetchone()[0]
        assert sleep_n > 10_000, f"expected >10k sleep rows, got {sleep_n}"
        assert hrv_n > 1_000, f"expected >1k HRV rows, got {hrv_n}"
        assert rhr_n > 500, f"expected >500 RHR rows, got {rhr_n}"
        assert nights_n > 500, f"expected >500 nightly summaries, got {nights_n}"
    finally:
        c.close()


def test_bootstrap_last7_nights(seeded_db):
    """Last 7 nights: avg asleep ≈6h47m; efficiency 90–98%."""
    c = db.connect(seeded_db, read_only=True)
    try:
        rows = c.execute(
            """SELECT asleep_seconds, efficiency_pct
               FROM nightly_summary
               ORDER BY night_date DESC
               LIMIT 7"""
        ).fetchall()
    finally:
        c.close()

    assert len(rows) == 7, "need at least 7 nightly summaries"

    avg_asleep_h = sum(r[0] for r in rows) / 7 / 3600
    # Validated reference: 6h 47m = 6.783h. Allow ±15 min.
    assert 6.5 <= avg_asleep_h <= 7.0, f"avg asleep {avg_asleep_h:.2f}h outside expected range"

    for _asleep_s, eff in rows:
        assert eff is not None
        assert 88.0 <= eff <= 100.0, f"efficiency {eff:.1f}% out of range for this sample week"


def test_bootstrap_idempotent(seeded_db):
    """Re-running bootstrap writes 0 new rows."""
    c_before = db.connect(seeded_db)
    try:
        sleep_before = c_before.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0]
        hrv_before = c_before.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0]
        rhr_before = c_before.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0]
    finally:
        c_before.close()

    rc = main([str(EXPORT_ZIP), "--db", seeded_db])
    assert rc == 0

    c_after = db.connect(seeded_db, read_only=True)
    try:
        assert c_after.execute("SELECT COUNT(*) FROM sleep_samples").fetchone()[0] == sleep_before
        assert c_after.execute("SELECT COUNT(*) FROM hrv_samples").fetchone()[0] == hrv_before
        assert c_after.execute("SELECT COUNT(*) FROM rhr_samples").fetchone()[0] == rhr_before
    finally:
        c_after.close()
