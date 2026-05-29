"""E2E tests for the Stage 4 orchestrator pipeline.

Drives the full pipeline end-to-end through real code — handle_issue(),
_poll_sessions(), _poll_prs() — with only the external API clients mocked.
No patching of orchestrator or poller internals.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.db import utcnow_iso
from app.devin_client import DevinClient, DevinAPIError, SessionResponse
from app.github_client import GitHubClient, Commit, CIRun, Issue
from app.orchestrator import handle_issue
from app.poller import _poll_sessions, _poll_prs
from app.shared.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**overrides) -> Config:
    defaults = dict(
        github_token="t",
        github_repo="o/r",
        github_webhook_secret=None,
        devin_api_key="k",
        devin_org_id="org-test",
        devin_daily_limit=10,
        pause=False,
        db_path=":memory:",
        devin_session_cost_limit_usd=None,
        devin_session_time_limit_minutes=None,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _issue(number=1, complexity="definite", labels=None):
    labels = labels or [
        "auto-remediate",
        f"complexity:{complexity}",
        "source:pip-audit",
        "severity:high",
    ]
    return Issue(number=number, title="Upgrade urllib3", labels=labels, body="Fix CVE.")


def _seed_issue(db, issue_id=1):
    with db:
        db.execute(
            "INSERT OR IGNORE INTO issues (issue_id, title, complexity, source, state, created_at) VALUES (?,?,?,?,?,?)",
            (issue_id, "Test", "definite", "pip-audit", "open", utcnow_iso()),
        )


def _seed_session(db, session_id, issue_id=1, status="running", pr_number=None):
    _seed_issue(db, issue_id)
    with db:
        db.execute(
            "INSERT INTO sessions (session_id, issue_id, status, created_at, pr_number) VALUES (?,?,?,?,?)",
            (session_id, issue_id, status, utcnow_iso(), pr_number),
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_full_pipeline_definite_issue(mem_db):
    """handle_issue creates session → _poll_sessions detects completion → _poll_prs records CI pass."""
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg()

    # Step 1: handle_issue creates a running session
    devin.create_session.return_value = "sess-abc"
    handle_issue(_issue(number=1, complexity="definite"), mem_db, devin, gh, cfg)

    session_row = mem_db.execute(
        "SELECT status FROM sessions WHERE session_id='sess-abc'"
    ).fetchone()
    assert session_row is not None
    assert session_row["status"] == "running"

    # Step 2: _poll_sessions detects completion with a PR URL
    devin.get_session.return_value = SessionResponse(
        session_id="sess-abc",
        status="completed",
        cost_usd=1.50,
        session_url="https://app.devin.ai/s/sess-abc",
        pr_url="https://github.com/o/r/pull/42",
        output=None,
    )
    _poll_sessions(mem_db, devin)

    session_row = mem_db.execute(
        "SELECT status, pr_number FROM sessions WHERE session_id='sess-abc'"
    ).fetchone()
    assert session_row["status"] == "completed"
    assert session_row["pr_number"] == 42

    completed_event = mem_db.execute(
        "SELECT event_type FROM events WHERE session_id='sess-abc' AND event_type='session.completed'"
    ).fetchone()
    assert completed_event is not None

    # Step 3: _poll_prs records CI pass + all Devin commits (no human intervention)
    gh.get_pr_commits.return_value = [
        Commit(sha="aaa", author="Devin"),
        Commit(sha="bbb", author="devin-ai"),
    ]
    gh.get_latest_ci_run.return_value = CIRun(
        run_id=1,
        status="completed",
        conclusion="success",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T01:00:00Z",
    )
    _poll_prs(mem_db, gh)

    final_row = mem_db.execute(
        "SELECT ci_first_pass, human_intervened FROM sessions WHERE session_id='sess-abc'"
    ).fetchone()
    assert final_row["ci_first_pass"] == 1
    assert final_row["human_intervened"] is None or final_row["human_intervened"] == 0

    ci_event = mem_db.execute(
        "SELECT event_type FROM events WHERE pr_number=42 AND event_type='pr.ci_completed'"
    ).fetchone()
    assert ci_event is not None


@pytest.mark.unit
def test_pipeline_ambiguous_issue_declined(mem_db):
    """Ambiguous issue is declined: session.declined event, needs-human-scoping label added, no Devin call."""
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg()

    issue = _issue(number=5, complexity="ambiguous")
    handle_issue(issue, mem_db, devin, gh, cfg)

    # No Devin session created
    devin.create_session.assert_not_called()

    # Label added on GitHub
    gh.add_label.assert_called_once_with(5, "needs-human-scoping")

    # session.declined event inserted
    event = mem_db.execute(
        "SELECT event_type FROM events WHERE issue_id=5 AND event_type='session.declined'"
    ).fetchone()
    assert event is not None

    # No sessions row created
    row = mem_db.execute("SELECT COUNT(*) FROM sessions WHERE issue_id=5").fetchone()
    assert row[0] == 0


@pytest.mark.unit
def test_pipeline_pause_blocks_dispatch(mem_db):
    """When pause=True, handle_issue returns early — no session created."""
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg(pause=True)

    handle_issue(_issue(number=2), mem_db, devin, gh, cfg)

    devin.create_session.assert_not_called()
    row = mem_db.execute("SELECT COUNT(*) FROM sessions").fetchone()
    assert row[0] == 0


@pytest.mark.unit
def test_pipeline_daily_limit_blocks_dispatch(mem_db):
    """When devin_daily_limit=0, handle_issue returns early — no session created."""
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg(devin_daily_limit=0)

    handle_issue(_issue(number=3), mem_db, devin, gh, cfg)

    devin.create_session.assert_not_called()
    row = mem_db.execute("SELECT COUNT(*) FROM sessions").fetchone()
    assert row[0] == 0


@pytest.mark.unit
def test_pipeline_session_fails(mem_db):
    """handle_issue creates session, _poll_sessions sees 'failed' → status=failed, session.failed event."""
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg()

    devin.create_session.return_value = "sess-fail"
    handle_issue(_issue(number=10, complexity="definite"), mem_db, devin, gh, cfg)

    devin.get_session.return_value = SessionResponse(
        session_id="sess-fail",
        status="failed",
        cost_usd=0.50,
        session_url="https://app.devin.ai/s/sess-fail",
        pr_url=None,
        output=None,
    )
    _poll_sessions(mem_db, devin)

    row = mem_db.execute(
        "SELECT status FROM sessions WHERE session_id='sess-fail'"
    ).fetchone()
    assert row["status"] == "failed"

    event = mem_db.execute(
        "SELECT event_type FROM events WHERE session_id='sess-fail' AND event_type='session.failed'"
    ).fetchone()
    assert event is not None


@pytest.mark.unit
def test_pipeline_session_blocked(mem_db):
    """_poll_sessions sees 'blocked' → status=blocked, session.blocked event."""
    _seed_session(mem_db, session_id="sess-block", issue_id=20, status="running")

    devin = MagicMock(spec=DevinClient)
    devin.get_session.return_value = SessionResponse(
        session_id="sess-block",
        status="blocked",
        cost_usd=0.25,
        session_url="https://app.devin.ai/s/sess-block",
        pr_url=None,
        output=None,
    )
    _poll_sessions(mem_db, devin)

    row = mem_db.execute(
        "SELECT status FROM sessions WHERE session_id='sess-block'"
    ).fetchone()
    assert row["status"] == "blocked"

    event = mem_db.execute(
        "SELECT event_type FROM events WHERE session_id='sess-block' AND event_type='session.blocked'"
    ).fetchone()
    assert event is not None


@pytest.mark.unit
def test_pipeline_human_commit_detected(mem_db):
    """_poll_prs with commits including a non-Devin author → human_intervened=1, pr.human_commit event."""
    _seed_session(mem_db, session_id="sess-human", issue_id=30, status="completed", pr_number=99)

    gh = MagicMock(spec=GitHubClient)
    gh.get_pr_commits.return_value = [
        Commit(sha="aaa", author="Devin"),
        Commit(sha="bbb", author="Alice"),  # human commit
    ]
    gh.get_latest_ci_run.return_value = None

    _poll_prs(mem_db, gh)

    row = mem_db.execute(
        "SELECT human_intervened FROM sessions WHERE session_id='sess-human'"
    ).fetchone()
    assert row["human_intervened"] == 1

    event = mem_db.execute(
        "SELECT event_type FROM events WHERE pr_number=99 AND event_type='pr.human_commit'"
    ).fetchone()
    assert event is not None


@pytest.mark.unit
def test_pipeline_ci_failure_recorded(mem_db):
    """_poll_prs with CI 'failure' conclusion → ci_first_pass=0, pr.ci_completed event."""
    _seed_session(mem_db, session_id="sess-ci-fail", issue_id=40, status="completed", pr_number=77)

    gh = MagicMock(spec=GitHubClient)
    gh.get_pr_commits.return_value = [Commit(sha="aaa", author="Devin")]
    gh.get_latest_ci_run.return_value = CIRun(
        run_id=55,
        status="completed",
        conclusion="failure",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T01:00:00Z",
    )

    _poll_prs(mem_db, gh)

    row = mem_db.execute(
        "SELECT ci_first_pass FROM sessions WHERE session_id='sess-ci-fail'"
    ).fetchone()
    assert row["ci_first_pass"] == 0

    event = mem_db.execute(
        "SELECT event_type FROM events WHERE pr_number=77 AND event_type='pr.ci_completed'"
    ).fetchone()
    assert event is not None


@pytest.mark.unit
def test_pipeline_idempotent_events(mem_db):
    """Calling handle_issue twice for the same issue creates only one session (second call sees active session)."""
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg()

    devin.create_session.return_value = "sess-idem"

    issue = _issue(number=50, complexity="definite")

    # First call — should create session
    handle_issue(issue, mem_db, devin, gh, cfg)
    assert devin.create_session.call_count == 1

    # Second call — active session exists, should return early
    handle_issue(issue, mem_db, devin, gh, cfg)
    assert devin.create_session.call_count == 1  # still only 1 call

    # Only one session row should exist
    count = mem_db.execute(
        "SELECT COUNT(*) FROM sessions WHERE issue_id=50"
    ).fetchone()[0]
    assert count == 1
