# Test Suite Design

**Date:** 2026-05-27
**Scope:** Unit + integration tests for stages 0–2 (retry, seeder, db/events)

## Structure

```
cognition_remediation/
    tests/
        conftest.py          # shared fixtures (mem_db, mock_session)
        test_retry.py        # app/shared/retry.py
        test_db.py           # app/db.py + app/events.py
        test_seeder.py       # scripts/seed_issues.py + scripts/reset_demo.py
    pytest.ini               # marker declarations, testpaths
```

## Markers

| Marker | Meaning | Requires network |
|---|---|---|
| `unit` | Fully mocked, offline-safe | No |
| `integration` | Hits real GitHub API or filesystem | Yes (.env) |

Run unit only: `pytest -m unit`
Run all: `pytest`
Run integration only: `pytest -m integration`

## Shared Fixtures (`conftest.py`)

- **`mem_db`** — `get_db(":memory:")` opened fresh per test; no file cleanup needed
- **`mock_session`** — `unittest.mock.MagicMock` shaped as `requests.Session`; pre-wired with a default 200 response

## `test_retry.py`

### Unit
- Succeeds immediately when function returns on first attempt
- Retries on HTTP 500, 502, 503, 504, 429
- Does NOT retry on 400 or 404
- Raises original exception after exhausting all attempts
- Honors `Retry-After` header (uses server value, capped at 120s)
- Detects rate-limit 403 via `X-RateLimit-Remaining: 0` → retries
- Ignores plain 403 (auth/permission) → does not retry

### Integration
- GET `https://api.github.com/rate_limit` with real token via `github_session`
- Confirms decorator is transparent for successful calls (no retry needed)
- Validates auth end-to-end without triggering a real rate limit

## `test_db.py`

### Unit
- Schema creates `issues`, `sessions`, `events` tables in `:memory:`
- `PRAGMA journal_mode` returns `wal` after `get_db`
- FK enforcement: inserting a session referencing a non-existent `issue_id` raises `IntegrityError`
- `utcnow_iso` returns a parseable ISO 8601 string in UTC
- `INSERT OR IGNORE` idempotency: second insert with same `idempotency_key` is a silent no-op
- `json.dumps default=str`: payload with `datetime` or `Path` serializes without raising
- All 12 typed event wrappers insert the correct `event_type` string

### Integration
- Write real `.db` file to `/tmp/cognition_test_<pid>.db`
- Full insert sequence: issue → session → event (verifies FK ordering)
- Read rows back and assert field values
- Cleanup file after test

## `test_seeder.py`

### Unit
- `_extract_idempotency_key` parses key from a valid cognition-meta block
- `_extract_idempotency_key` returns `None` when block is absent
- `_already_seeded` returns `True` when key appears in any body
- `_already_seeded` returns `False` when key is absent from all bodies
- `_collect_labels` deduplicates labels across issues
- `fetch_existing_issue_bodies` filters out items with `pull_request` key
- `fetch_existing_issue_bodies` follows pagination (stops when batch < per_page)
- `ensure_labels` treats 422 as success (label already exists)
- `load_issues` raises `ValueError` when `issues` key is not a list

### Integration
- Call `fetch_existing_issue_bodies` against real `hchang19/superset`
- Assert result is a list (may be empty); confirms auth + pagination works
- Read-only — no issues are created

## How to Run Tests

```bash
cd cognition_remediation

# Install deps (first time)
pip install -r requirements.txt
pip install pytest pytest-mock

# Unit tests only (offline, fast)
pytest -m unit -v

# Integration tests (requires .env with real credentials)
pytest -m integration -v

# All tests
pytest -v

# Single file
pytest tests/test_retry.py -v
```

Integration tests load `.env` automatically via `python-dotenv`. Ensure `GITHUB_TOKEN` and `GITHUB_REPO` are set.

## Documentation Location

Test running instructions also live in `docs/testing.md` (committed alongside the test files) so contributors find them without reading this spec.
