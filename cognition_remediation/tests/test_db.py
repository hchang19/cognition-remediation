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
def test_wal_mode_enabled(tmp_path):
    # WAL mode is unsupported for :memory: databases; test with a real file.
    db = get_db(str(tmp_path / "wal_test.db"))
    try:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        db.close()


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
