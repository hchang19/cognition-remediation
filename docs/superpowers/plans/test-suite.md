# Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add unit + integration tests for retry.py, db.py/events.py, and seed_issues.py, plus a `docs/testing.md` runbook.

**Architecture:** Flat `tests/` directory under `cognition_remediation/` with pytest markers (`unit`, `integration`) to separate offline and network-dependent tests. Shared fixtures live in `conftest.py`. Integration tests load real credentials from `.env` via python-dotenv and skip gracefully when credentials are absent.

**Tech Stack:** pytest, unittest.mock, python-dotenv, requests (already in requirements.txt)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `cognition_remediation/pytest.ini` | marker declarations, testpaths |
| Create | `cognition_remediation/tests/__init__.py` | package marker |
| Create | `cognition_remediation/tests/conftest.py` | `mem_db`, `mock_session` fixtures |
| Create | `cognition_remediation/tests/test_retry.py` | unit + integration for retry.py |
| Create | `cognition_remediation/tests/test_db.py` | unit + integration for db.py + events.py |
| Create | `cognition_remediation/tests/test_seeder.py` | unit + integration for seed_issues.py |
| Create | `cognition_remediation/docs/testing.md` | runbook: how to install and run tests |

---

## Task 1: pytest.ini + conftest.py + package marker

**Files:**
- Create: `cognition_remediation/pytest.ini`
- Create: `cognition_remediation/tests/__init__.py`
- Create: `cognition_remediation/tests/conftest.py`

- [ ] **Step 1: Install pytest and pytest-mock**

```bash
cd cognition_remediation
pip install pytest pytest-mock
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
testpaths = tests
markers =
    unit: offline, fully mocked — no network or filesystem side-effects
    integration: requires network and .env credentials
```

Save to `cognition_remediation/pytest.ini`.

- [ ] **Step 3: Create tests/__init__.py**

Empty file — makes `tests` a package so relative imports work.

Save to `cognition_remediation/tests/__init__.py`.

- [ ] **Step 4: Create conftest.py**

```python
import pytest
import requests
from unittest.mock import MagicMock

from app.db import get_db


@pytest.fixture
def mem_db():
    conn = get_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def mock_session():
    session = MagicMock(spec=requests.Session)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}
    response.headers = {}
    response.raise_for_status.return_value = None
    session.get.return_value = response
    session.post.return_value = response
    return session
```

Save to `cognition_remediation/tests/conftest.py`.

- [ ] **Step 5: Verify pytest collects without errors**

```bash
cd cognition_remediation
pytest --collect-only
```

Expected: `no tests ran` with zero errors.

- [ ] **Step 6: Commit**

```bash
git add pytest.ini tests/__init__.py tests/conftest.py
git commit -m "test: add pytest config and shared fixtures"
```

---

## Task 2: test_retry.py — unit tests

**Files:**
- Create: `cognition_remediation/tests/test_retry.py`

- [ ] **Step 1: Create test_retry.py with unit tests**

```python
import pytest
import requests
from unittest.mock import MagicMock, patch

from app.shared.retry import with_retry


def _http_error(status_code: int, headers: dict | None = None) -> requests.HTTPError:
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    return requests.HTTPError(response=r)


@pytest.mark.unit
def test_success_on_first_attempt():
    call_count = 0

    @with_retry()
    def fn():
        nonlocal call_count
        call_count += 1
        return "ok"

    assert fn() == "ok"
    assert call_count == 1


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429])
def test_retries_on_transient_errors(status_code):
    exc = _http_error(status_code)
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise exc
        return "ok"

    with patch("time.sleep"):
        result = fn()

    assert result == "ok"
    assert call_count == 3


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [400, 404])
def test_no_retry_on_client_errors(status_code):
    exc = _http_error(status_code)
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 1


@pytest.mark.unit
def test_raises_after_exhausting_attempts():
    exc = _http_error(500)
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with patch("time.sleep"), pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 3


@pytest.mark.unit
def test_retry_after_header_honored():
    exc = _http_error(500, headers={"Retry-After": "5"})
    sleep_calls: list[float] = []

    @with_retry(max_attempts=2, base_delay=1.0)
    def fn():
        raise exc

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(requests.HTTPError):
            fn()

    assert sleep_calls[0] == 5.0


@pytest.mark.unit
def test_retry_after_header_capped_at_120s():
    exc = _http_error(500, headers={"Retry-After": "999"})
    sleep_calls: list[float] = []

    @with_retry(max_attempts=2, base_delay=1.0)
    def fn():
        raise exc

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(requests.HTTPError):
            fn()

    assert sleep_calls[0] == 120.0


@pytest.mark.unit
def test_rate_limit_403_with_remaining_zero_retries():
    exc = _http_error(403, headers={"X-RateLimit-Remaining": "0"})
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with patch("time.sleep"), pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 3


@pytest.mark.unit
def test_plain_403_does_not_retry():
    exc = _http_error(403)  # no rate-limit headers
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 1


@pytest.mark.integration
def test_github_rate_limit_endpoint_succeeds():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")

    from app.shared.github_session import github_session

    session = github_session(token)

    @with_retry()
    def get_rate_limit():
        r = session.get("https://api.github.com/rate_limit")
        r.raise_for_status()
        return r.json()

    data = get_rate_limit()
    assert "rate" in data
    assert "limit" in data["rate"]
```

- [ ] **Step 2: Run unit tests and verify all pass**

```bash
cd cognition_remediation
pytest tests/test_retry.py -m unit -v
```

Expected: 10 tests pass (parametrized statuses expand the count).

- [ ] **Step 3: Run integration test**

```bash
pytest tests/test_retry.py -m integration -v
```

Expected: 1 test passes.

- [ ] **Step 4: Commit**

```bash
git add tests/test_retry.py
git commit -m "test: retry.py unit + integration tests"
```

---

## Task 3: test_db.py — unit + integration tests

**Files:**
- Create: `cognition_remediation/tests/test_db.py`

- [ ] **Step 1: Create test_db.py**

```python
import pytest
import sqlite3
from datetime import datetime
from pathlib import Path

from app.db import get_db, utcnow_iso
from app.events import (
    insert_issue_created, insert_issue_closed, insert_issue_reopened,
    insert_session_started, insert_session_completed, insert_session_failed,
    insert_session_blocked, insert_session_declined, insert_session_start_failed,
    insert_pr_opened, insert_pr_human_commit, insert_pr_ci_completed,
    EVENT_ISSUE_CREATED, EVENT_ISSUE_CLOSED, EVENT_ISSUE_REOPENED,
    EVENT_SESSION_STARTED, EVENT_SESSION_COMPLETED, EVENT_SESSION_FAILED,
    EVENT_SESSION_BLOCKED, EVENT_SESSION_DECLINED, EVENT_SESSION_START_FAILED,
    EVENT_PR_OPENED, EVENT_PR_HUMAN_COMMIT, EVENT_PR_CI_COMPLETED,
)


def _insert_issue(db, issue_id: int = 1) -> None:
    with db:
        db.execute(
            "INSERT INTO issues (issue_id, title, complexity, source, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (issue_id, "Test issue", "definite", "manual", "open", utcnow_iso()),
        )


def _insert_session(db, session_id: str = "sess-1", issue_id: int = 1) -> None:
    with db:
        db.execute(
            "INSERT INTO sessions (session_id, issue_id, status, created_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, issue_id, "running", utcnow_iso()),
        )


@pytest.mark.unit
def test_schema_creates_all_tables(mem_db):
    tables = {
        row[0]
        for row in mem_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"issues", "sessions", "events"} <= tables


@pytest.mark.unit
def test_wal_mode_enabled(mem_db):
    mode = mem_db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


@pytest.mark.unit
def test_foreign_key_enforcement(mem_db):
    with pytest.raises(sqlite3.IntegrityError):
        with mem_db:
            mem_db.execute(
                "INSERT INTO sessions (session_id, issue_id, status, created_at)"
                " VALUES (?, ?, ?, ?)",
                ("s1", 9999, "running", utcnow_iso()),
            )


@pytest.mark.unit
def test_utcnow_iso_is_parseable_utc():
    ts = utcnow_iso()
    dt = datetime.fromisoformat(ts)
    assert dt.tzinfo is not None


@pytest.mark.unit
def test_insert_or_ignore_idempotency(mem_db):
    _insert_issue(mem_db)
    insert_issue_created(mem_db, issue_id=1, idempotency_key="key-1")
    insert_issue_created(mem_db, issue_id=1, idempotency_key="key-1")
    count = mem_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_payload_with_non_json_native_types(mem_db):
    _insert_issue(mem_db)
    payload = {"ts": datetime(2024, 1, 1), "path": Path("/tmp/test")}
    insert_issue_created(
        mem_db, issue_id=1, idempotency_key="key-nonstd", payload=payload
    )
    row = mem_db.execute(
        "SELECT payload FROM events WHERE idempotency_key='key-nonstd'"
    ).fetchone()
    assert row is not None
    assert "2024" in row[0]


@pytest.mark.unit
@pytest.mark.parametrize("wrapper,event_type,kwargs", [
    (insert_issue_created,        EVENT_ISSUE_CREATED,        {"issue_id": 1, "idempotency_key": "ik-01"}),
    (insert_issue_closed,         EVENT_ISSUE_CLOSED,         {"issue_id": 1, "idempotency_key": "ik-02"}),
    (insert_issue_reopened,       EVENT_ISSUE_REOPENED,       {"issue_id": 1, "idempotency_key": "ik-03"}),
    (insert_session_declined,     EVENT_SESSION_DECLINED,     {"issue_id": 1, "idempotency_key": "ik-10"}),
    (insert_session_start_failed, EVENT_SESSION_START_FAILED, {"issue_id": 1, "idempotency_key": "ik-11"}),
])
def test_issue_event_wrappers(mem_db, wrapper, event_type, kwargs):
    _insert_issue(mem_db)
    wrapper(mem_db, **kwargs)
    row = mem_db.execute(
        "SELECT event_type FROM events WHERE idempotency_key=?",
        (kwargs["idempotency_key"],),
    ).fetchone()
    assert row[0] == event_type


@pytest.mark.unit
@pytest.mark.parametrize("wrapper,event_type,kwargs", [
    (insert_session_started,   EVENT_SESSION_STARTED,   {"issue_id": 1, "session_id": "s1", "idempotency_key": "ik-04"}),
    (insert_session_completed, EVENT_SESSION_COMPLETED, {"issue_id": 1, "session_id": "s1", "idempotency_key": "ik-05"}),
    (insert_session_failed,    EVENT_SESSION_FAILED,    {"issue_id": 1, "session_id": "s1", "idempotency_key": "ik-06"}),
    (insert_session_blocked,   EVENT_SESSION_BLOCKED,   {"issue_id": 1, "session_id": "s1", "idempotency_key": "ik-07"}),
])
def test_session_event_wrappers(mem_db, wrapper, event_type, kwargs):
    _insert_issue(mem_db)
    _insert_session(mem_db, session_id="s1", issue_id=1)
    wrapper(mem_db, **kwargs)
    row = mem_db.execute(
        "SELECT event_type FROM events WHERE idempotency_key=?",
        (kwargs["idempotency_key"],),
    ).fetchone()
    assert row[0] == event_type


@pytest.mark.unit
@pytest.mark.parametrize("wrapper,event_type,kwargs", [
    (insert_pr_opened,       EVENT_PR_OPENED,       {"issue_id": 1, "session_id": "s1", "pr_number": 42, "idempotency_key": "ik-08"}),
    (insert_pr_human_commit, EVENT_PR_HUMAN_COMMIT, {"pr_number": 42, "idempotency_key": "ik-09"}),
    (insert_pr_ci_completed, EVENT_PR_CI_COMPLETED, {"pr_number": 42, "idempotency_key": "ik-12"}),
])
def test_pr_event_wrappers(mem_db, wrapper, event_type, kwargs):
    _insert_issue(mem_db)
    _insert_session(mem_db, session_id="s1", issue_id=1)
    wrapper(mem_db, **kwargs)
    row = mem_db.execute(
        "SELECT event_type FROM events WHERE idempotency_key=?",
        (kwargs["idempotency_key"],),
    ).fetchone()
    assert row[0] == event_type


@pytest.mark.integration
def test_real_db_full_insert_sequence(tmp_path):
    db_path = str(tmp_path / "cognition_test.db")
    db = get_db(db_path)
    try:
        with db:
            db.execute(
                "INSERT INTO issues (issue_id, title, complexity, source, state, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (1, "CVE-integration-test", "definite", "pip-audit", "open", utcnow_iso()),
            )
        with db:
            db.execute(
                "INSERT INTO sessions (session_id, issue_id, status, created_at)"
                " VALUES (?, ?, ?, ?)",
                ("sess-int", 1, "running", utcnow_iso()),
            )
        insert_session_started(db, issue_id=1, session_id="sess-int", idempotency_key="int-1")
        insert_session_completed(
            db, issue_id=1, session_id="sess-int",
            idempotency_key="int-2", pr_number=99,
        )

        events = db.execute(
            "SELECT event_type, pr_number FROM events ORDER BY id"
        ).fetchall()
        assert events[0]["event_type"] == EVENT_SESSION_STARTED
        assert events[1]["event_type"] == EVENT_SESSION_COMPLETED
        assert events[1]["pr_number"] == 99
    finally:
        db.close()
```

- [ ] **Step 2: Run unit tests**

```bash
cd cognition_remediation
pytest tests/test_db.py -m unit -v
```

Expected: ~18 tests pass (parametrized wrappers expand the count).

- [ ] **Step 3: Run integration test**

```bash
pytest tests/test_db.py -m integration -v
```

Expected: 1 test passes.

- [ ] **Step 4: Commit**

```bash
git add tests/test_db.py
git commit -m "test: db.py + events.py unit + integration tests"
```

---

## Task 4: test_seeder.py — unit + integration tests

**Files:**
- Create: `cognition_remediation/tests/test_seeder.py`

- [ ] **Step 1: Create test_seeder.py**

```python
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.seed_issues import (
    _already_seeded,
    _collect_labels,
    _extract_idempotency_key,
    ensure_labels,
    fetch_existing_issue_bodies,
    load_issues,
)


SAMPLE_BODY = (
    "Some issue text.\n"
    '<!-- cognition-meta {"idempotency_key": "cve-urllib3-2024-01", "complexity": "definite"} -->\n'
    "More text."
)


@pytest.mark.unit
def test_extract_idempotency_key_valid():
    assert _extract_idempotency_key(SAMPLE_BODY) == "cve-urllib3-2024-01"


@pytest.mark.unit
def test_extract_idempotency_key_missing():
    assert _extract_idempotency_key("No meta block here") is None


@pytest.mark.unit
def test_already_seeded_true():
    bodies = ["some body", SAMPLE_BODY, "other body"]
    assert _already_seeded("cve-urllib3-2024-01", bodies) is True


@pytest.mark.unit
def test_already_seeded_false():
    bodies = ["some body", "other body"]
    assert _already_seeded("cve-urllib3-2024-01", bodies) is False


@pytest.mark.unit
def test_collect_labels_deduplicates():
    issues = [
        {"labels": ["auto-remediate", "complexity:definite"]},
        {"labels": ["auto-remediate", "type:vulnerability"]},
    ]
    labels = _collect_labels(issues)
    assert len(labels) == len(set(labels))
    assert "auto-remediate" in labels
    assert len(labels) == 3


@pytest.mark.unit
def test_fetch_existing_issue_bodies_filters_prs(mock_session):
    items = [
        {"body": "issue body", "number": 1},
        {"body": "pr body", "number": 2, "pull_request": {"url": "https://..."}},
    ]
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = items
    response.raise_for_status.return_value = None
    mock_session.get.return_value = response

    bodies = fetch_existing_issue_bodies(mock_session, "owner/repo")
    assert "issue body" in bodies
    assert "pr body" not in bodies


@pytest.mark.unit
def test_fetch_existing_issue_bodies_paginates(mock_session):
    page1 = [{"body": f"body-{i}", "number": i} for i in range(100)]
    page2 = [{"body": "last-body", "number": 100}]

    def make_response(items):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = items
        r.raise_for_status.return_value = None
        return r

    mock_session.get.side_effect = [make_response(page1), make_response(page2)]

    bodies = fetch_existing_issue_bodies(mock_session, "owner/repo")
    assert len(bodies) == 101
    assert "last-body" in bodies


@pytest.mark.unit
def test_ensure_labels_treats_422_as_success(mock_session):
    response = MagicMock()
    response.status_code = 422
    response.raise_for_status.return_value = None
    mock_session.post.return_value = response
    ensure_labels(mock_session, "owner/repo", ["auto-remediate"])


@pytest.mark.unit
def test_load_issues_raises_on_non_list(tmp_path):
    bad_yaml = tmp_path / "issues.yml"
    bad_yaml.write_text("issues: not-a-list\n")
    with pytest.raises(ValueError, match="must be a list"):
        load_issues(bad_yaml)


@pytest.mark.integration
def test_fetch_existing_issue_bodies_real_github():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")

    from app.shared.github_session import github_session
    session = github_session(token)
    bodies = fetch_existing_issue_bodies(session, repo)
    assert isinstance(bodies, list)
```

- [ ] **Step 2: Run unit tests**

```bash
cd cognition_remediation
pytest tests/test_seeder.py -m unit -v
```

Expected: 9 tests pass.

- [ ] **Step 3: Run integration test**

```bash
pytest tests/test_seeder.py -m integration -v
```

Expected: 1 test passes (read-only, no issues created).

- [ ] **Step 4: Commit**

```bash
git add tests/test_seeder.py
git commit -m "test: seed_issues.py unit + integration tests"
```

---

## Task 5: docs/testing.md

**Files:**
- Create: `cognition_remediation/docs/testing.md`

- [ ] **Step 1: Create docs/ directory and testing.md**

```bash
mkdir -p cognition_remediation/docs
```

Create `cognition_remediation/docs/testing.md`:

```markdown
# Testing

Tests live in `cognition_remediation/tests/` and use two pytest markers to separate offline and network-dependent runs.

## Markers

| Marker | What it tests | Network required |
|---|---|---|
| `unit` | Fully mocked, offline-safe | No |
| `integration` | Real GitHub API or real filesystem | Yes (`.env`) |

## Setup

```bash
cd cognition_remediation
pip install -r requirements.txt
pip install pytest pytest-mock
```

Integration tests load `.env` automatically via `python-dotenv`. Ensure at minimum:

```
GITHUB_TOKEN=<your fine-grained PAT>
GITHUB_REPO=hchang19/superset
```

## Running Tests

```bash
# Fast offline suite — no credentials needed
pytest -m unit -v

# Integration suite — requires .env
pytest -m integration -v

# Full suite
pytest -v

# Single file
pytest tests/test_retry.py -v
pytest tests/test_db.py -v
pytest tests/test_seeder.py -v
```

## Coverage by Module

| Source file | Test file | Unit | Integration |
|---|---|---|---|
| `app/shared/retry.py` | `tests/test_retry.py` | 8 cases | 1 case |
| `app/db.py` + `app/events.py` | `tests/test_db.py` | ~18 cases | 1 case |
| `scripts/seed_issues.py` | `tests/test_seeder.py` | 9 cases | 1 case |

## Adding Tests

- Place new test files in `cognition_remediation/tests/`.
- Mark every test with `@pytest.mark.unit` or `@pytest.mark.integration`.
- Integration tests must call `pytest.skip(...)` when required env vars are absent.
- Shared fixtures go in `tests/conftest.py`.
```

- [ ] **Step 2: Run full test suite**

```bash
cd cognition_remediation
pytest -v
```

Expected: all unit + integration tests pass.

- [ ] **Step 3: Commit**

```bash
git add docs/testing.md
git commit -m "docs: add testing.md runbook (setup, markers, coverage table)"
```

---

## Task 6: Push branch

- [ ] **Step 1: Push to remote**

```bash
git push origin <current-branch>
```
