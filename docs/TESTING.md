# Testing Discipline (MANDATORY)

This project follows a strict test-driven workflow. Claude Code MUST adhere to
this for every change. "It compiles" is not "it works." No phase in
`docs/BUILD_ORDER.md` is complete until its tests are written AND green.

## The rules

1. **Test-first for every unit of logic.** Before implementing a function with
   real behavior (parsing, dedup, night assignment, efficiency math, auth, MCP
   queries), write or extend a test that pins the expected behavior. Watch it fail,
   then implement until it passes.

2. **No stub ships without a test that proves it's filled.** Every `TODO(claude-code)`
   you resolve must be accompanied by a test asserting the new behavior. If you
   implement `db.insert_sleep`, `test_ingest_is_idempotent` must exercise it.

3. **Validate at every step, not just at the end.** After each function:
   - `ruff check` and `ruff format --check` clean
   - `pytest` green (the whole suite, not just the new test)
   - For endpoints: a FastAPI `TestClient` test covering success + each auth branch
   - For SQL: assert against a temp DuckDB with known fixtures

4. **Idempotency is a first-class test.** Any write path gets a "run it twice,
   assert no duplicate rows and stable counts" test. This is the single most
   important invariant in the system.

5. **Boundary/edge cases are required, not optional.** Specifically:
   - Night assignment exactly at 12:00 local (-> next day) and across DST changes.
   - Efficiency math with zero time-in-bed (no divide-by-zero).
   - HRV averaging with zero samples (night with no HRV -> null, not crash).
   - Empty ingest payload (valid, writes nothing, recomputes nothing).
   - Auth: missing header, malformed header, wrong token, correct token, dev bypass.

6. **Bootstrap correctness check.** After implementing writes, run the bootstrap
   against a real `export.zip` and assert the last 7 nights match the numbers Owner
   already validated (≈6h47m avg asleep; per-night efficiency 90–98% for the
   sample week). Encode this as a test that runs only when a sample export is
   present (skip otherwise), so CI without the file still passes.

7. **MCP tools get contract tests.** Each tool: seed a temp DuckDB, call the tool
   function directly (not over the wire), assert the returned shape and values.
   `get_recovery_status` needs cases for green, yellow, and red.

8. **Regression guard.** When you fix a bug, first write the failing test that
   reproduces it, then fix. Leave the test in place.

## Tooling

- `pytest` for Python (backend, bootstrap, mcp). Fixtures use temp-dir DuckDB.
- `httpx` + FastAPI `TestClient` for endpoint tests.
- `ruff` for lint + format. Config in each `pyproject.toml`.
- iOS: add `XCTest` targets for the stage-mapping function and payload JSON
  encoding (pure logic, testable without a device). Document that UI/HealthKit
  integration is validated manually on-device (can't run in CI without a Mac+sim).

## Definition of done (per function / endpoint / tool)

- [ ] Behavior pinned by at least one test written for it.
- [ ] All edge cases from rule 5 relevant to it are covered.
- [ ] `pytest` green across the whole suite.
- [ ] `ruff check` and `ruff format --check` clean.
- [ ] If it's a write path: idempotency test present and green.

## Suggested test layout

```
backend/tests/
  test_auth.py         # bearer branches
  test_db_inserts.py   # idempotent inserts per type
  test_night_logic.py  # assign_to_night, DST, noon boundary
  test_summary.py      # efficiency, stage sums, hrv avg, edge cases
  test_endpoints.py    # /ingest, /stats via TestClient incl. auth
  test_bootstrap.py    # real-export spot check (skip if no sample file)
mcp/tests/
  test_tools.py        # each tool against a seeded temp DuckDB
```

## CI (recommended, even if minimal)

Add a GitHub Actions workflow that runs `ruff` + `pytest` for `backend/` and
`mcp/` on every push. Keep it green. The real-export test self-skips when the
sample file isn't present, so CI passes without shipping personal data.
