import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from app.db import get_db, utcnow_iso
from app.github_client import Issue
from app.devin_client import DevinClient, DevinAPIError, SessionResponse
from app.github_client import GitHubClient
from app.shared.config import Config
from app.orchestrator import handle_issue


def _cfg(**overrides) -> Config:
    defaults = dict(
        github_token="t", github_repo="o/r", github_webhook_secret=None,
        devin_api_key="k", devin_org_id="org-test", devin_daily_limit=10, pause=False, db_path=":memory:",
        devin_session_cost_limit_usd=None, devin_session_time_limit_minutes=None,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _issue(number=1, labels=None) -> Issue:
    return Issue(
        number=number,
        title="Upgrade urllib3",
        labels=labels or ["auto-remediate", "complexity:definite", "source:pip-audit", "severity:high"],
        body="Fix CVE.",
    )


def _seed_issue(db, issue_id=1):
    with db:
        db.execute(
            "INSERT OR IGNORE INTO issues (issue_id, title, complexity, source, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (issue_id, "Test", "definite", "pip-audit", "open", utcnow_iso()),
        )


@pytest.mark.unit
def test_handle_issue_paused_returns_early(mem_db):
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg(pause=True)

    handle_issue(_issue(), mem_db, devin, gh, cfg)

    devin.create_session.assert_not_called()


@pytest.mark.unit
def test_handle_issue_daily_limit_returns_early(mem_db):
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    cfg = _cfg(devin_daily_limit=0)

    handle_issue(_issue(), mem_db, devin, gh, cfg)

    devin.create_session.assert_not_called()


@pytest.mark.unit
def test_handle_issue_active_session_returns_early(mem_db):
    _seed_issue(mem_db, issue_id=1)
    with mem_db:
        mem_db.execute(
            "INSERT INTO sessions (session_id, issue_id, status, created_at) VALUES (?,?,?,?)",
            ("s-existing", 1, "running", utcnow_iso()),
        )
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)

    handle_issue(_issue(number=1), mem_db, devin, gh, _cfg())

    devin.create_session.assert_not_called()


@pytest.mark.unit
def test_handle_issue_ambiguous_declines_and_adds_label(mem_db):
    devin = MagicMock(spec=DevinClient)
    gh = MagicMock(spec=GitHubClient)
    issue = _issue(labels=["auto-remediate", "complexity:ambiguous"])

    handle_issue(issue, mem_db, devin, gh, _cfg())

    devin.create_session.assert_not_called()
    gh.add_label.assert_called_once_with(issue.number, "needs-human-scoping")
    row = mem_db.execute(
        "SELECT event_type FROM events WHERE issue_id=? AND event_type='session.declined'",
        (issue.number,),
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "session.declined"


@pytest.mark.unit
def test_handle_issue_definite_creates_session(mem_db):
    devin = MagicMock(spec=DevinClient)
    devin.create_session.return_value = "sess-new"
    gh = MagicMock(spec=GitHubClient)

    handle_issue(_issue(), mem_db, devin, gh, _cfg())

    devin.create_session.assert_called_once()
    row = mem_db.execute("SELECT status FROM sessions WHERE session_id='sess-new'").fetchone()
    assert row is not None
    assert row["status"] == "running"


@pytest.mark.unit
def test_handle_issue_semi_definite_creates_session(mem_db):
    devin = MagicMock(spec=DevinClient)
    devin.create_session.return_value = "sess-semi"
    gh = MagicMock(spec=GitHubClient)
    issue = _issue(labels=["auto-remediate", "complexity:semi-definite", "source:manual"])

    handle_issue(issue, mem_db, devin, gh, _cfg())

    devin.create_session.assert_called_once()
    prompt_arg = devin.create_session.call_args[0][0]
    assert "root cause" in prompt_arg.lower()


@pytest.mark.unit
def test_handle_issue_session_start_failed_inserts_event(mem_db):
    devin = MagicMock(spec=DevinClient)
    devin.create_session.side_effect = DevinAPIError("timeout")
    gh = MagicMock(spec=GitHubClient)

    handle_issue(_issue(), mem_db, devin, gh, _cfg())

    row = mem_db.execute("SELECT event_type FROM events WHERE issue_id=1").fetchall()
    event_types = [r["event_type"] for r in row]
    assert "session.start_failed" in event_types
