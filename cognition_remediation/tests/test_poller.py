import pytest
from unittest.mock import MagicMock
from app.db import get_db, utcnow_iso
from app.devin_client import DevinClient, DevinAPIError, SessionResponse
from app.github_client import GitHubClient, Commit, CIRun
from app.poller import _poll_sessions, _poll_prs, _extract_pr_number


def _seed_issue(db, issue_id=1):
    with db:
        db.execute(
            "INSERT OR IGNORE INTO issues (issue_id, title, complexity, source, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (issue_id, "Test", "definite", "pip-audit", "open", utcnow_iso()),
        )


def _seed_session(db, session_id="s1", issue_id=1, status="running", pr_number=None):
    _seed_issue(db, issue_id)
    with db:
        db.execute(
            "INSERT INTO sessions (session_id, issue_id, status, created_at, pr_number)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, issue_id, status, utcnow_iso(), pr_number),
        )


@pytest.mark.unit
def test_extract_pr_number_from_url():
    assert _extract_pr_number("https://github.com/org/repo/pull/42") == 42


@pytest.mark.unit
def test_extract_pr_number_returns_none_for_none():
    assert _extract_pr_number(None) is None


@pytest.mark.unit
def test_poll_sessions_updates_status_on_change(mem_db):
    _seed_session(mem_db, session_id="s1", status="running")
    devin = MagicMock(spec=DevinClient)
    devin.get_session.return_value = SessionResponse(
        session_id="s1", status="completed",
        cost_usd=1.5, session_url="https://app.devin.ai/s/s1",
        pr_url="https://github.com/o/r/pull/9", output=None,
    )

    _poll_sessions(mem_db, devin)

    row = mem_db.execute("SELECT status, pr_number FROM sessions WHERE session_id='s1'").fetchone()
    assert row["status"] == "completed"
    assert row["pr_number"] == 9
    event = mem_db.execute("SELECT event_type FROM events WHERE session_id='s1'").fetchone()
    assert event["event_type"] == "session.completed"


@pytest.mark.unit
def test_poll_sessions_skips_unchanged_status(mem_db):
    _seed_session(mem_db, session_id="s2", status="running")
    devin = MagicMock(spec=DevinClient)
    devin.get_session.return_value = SessionResponse(
        session_id="s2", status="running",
        cost_usd=None, session_url=None, pr_url=None, output=None,
    )

    _poll_sessions(mem_db, devin)

    row = mem_db.execute("SELECT status FROM sessions WHERE session_id='s2'").fetchone()
    assert row["status"] == "running"
    count = mem_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


@pytest.mark.unit
def test_poll_sessions_handles_devin_api_error(mem_db):
    _seed_session(mem_db, session_id="s3", status="running")
    devin = MagicMock(spec=DevinClient)
    devin.get_session.side_effect = DevinAPIError("timeout")

    _poll_sessions(mem_db, devin)  # must not raise

    row = mem_db.execute("SELECT status FROM sessions WHERE session_id='s3'").fetchone()
    assert row["status"] == "running"


@pytest.mark.unit
def test_poll_prs_flags_human_commit(mem_db):
    _seed_session(mem_db, session_id="s4", status="completed", pr_number=5)
    gh = MagicMock(spec=GitHubClient)
    gh.get_pr_commits.return_value = [
        Commit(sha="abc", author="Devin"),
        Commit(sha="def", author="Alice"),
    ]
    gh.get_latest_ci_run.return_value = None

    _poll_prs(mem_db, gh)

    row = mem_db.execute("SELECT human_intervened FROM sessions WHERE session_id='s4'").fetchone()
    assert row["human_intervened"] == 1
    event = mem_db.execute("SELECT event_type FROM events WHERE pr_number=5").fetchone()
    assert event["event_type"] == "pr.human_commit"


@pytest.mark.unit
def test_poll_prs_records_ci_first_pass_success(mem_db):
    _seed_session(mem_db, session_id="s5", status="completed", pr_number=6)
    gh = MagicMock(spec=GitHubClient)
    gh.get_pr_commits.return_value = [Commit(sha="abc", author="Devin")]
    gh.get_latest_ci_run.return_value = CIRun(
        run_id=99, status="completed", conclusion="success",
        started_at="2024-01-01T00:00:00Z", completed_at="2024-01-01T01:00:00Z",
    )

    _poll_prs(mem_db, gh)

    row = mem_db.execute("SELECT ci_first_pass FROM sessions WHERE session_id='s5'").fetchone()
    assert row["ci_first_pass"] == 1
    event = mem_db.execute("SELECT event_type FROM events WHERE pr_number=6").fetchone()
    assert event["event_type"] == "pr.ci_completed"
