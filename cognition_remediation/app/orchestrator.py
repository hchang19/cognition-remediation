"""Issue routing and Devin session creation.

Business logic layer between webhook/poller and the Devin + GitHub APIs.
No HTTP calls — those are delegated to DevinClient and GitHubClient.

FK insertion order (enforced by SQLite):
  1. issues row  (INSERT OR IGNORE)
  2. sessions row
  3. events rows
"""

from __future__ import annotations

import sqlite3

from app.db import utcnow_iso
from app.devin_client import DevinClient, DevinAPIError
from app.events import (
    insert_issue_created,
    insert_session_declined,
    insert_session_start_failed,
    insert_session_started,
)
from app.github_client import GitHubClient, Issue
from app.prompts import definite_prompt, semi_definite_prompt
from app.shared.config import Config
from app.shared.logger import get_logger

logger = get_logger(__name__)


def _count_today_sessions(db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE created_at >= date('now')"
    ).fetchone()
    return row[0]


def _has_active_session(db: sqlite3.Connection, issue_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM sessions"
        " WHERE issue_id = ? AND status NOT IN ('completed', 'failed', 'blocked')",
        (issue_id,),
    ).fetchone() is not None


def _upsert_issue(db: sqlite3.Connection, issue: Issue, complexity: str, source: str, severity: str | None) -> None:
    with db:
        db.execute(
            "INSERT OR IGNORE INTO issues"
            " (issue_id, title, complexity, source, severity, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (issue.number, issue.title, complexity, source, severity, utcnow_iso()),
        )


def handle_issue(
    issue: Issue,
    db: sqlite3.Connection,
    devin: DevinClient,
    gh: GitHubClient,
    cfg: Config,
) -> None:
    """Route an issue to Devin or decline it based on complexity."""
    label_set = set(issue.labels)

    if cfg.pause:
        logger.info("orchestrator.paused", extra={"issue_id": issue.number})
        return

    if _count_today_sessions(db) >= cfg.devin_daily_limit:
        logger.warning("orchestrator.daily_limit_reached", extra={"issue_id": issue.number})
        return

    if _has_active_session(db, issue.number):
        logger.info("orchestrator.active_session_exists", extra={"issue_id": issue.number})
        return

    complexity = next(
        (l.split(":", 1)[1] for l in label_set if l.startswith("complexity:")),
        "ambiguous",
    )
    source = next(
        (l.split(":", 1)[1] for l in label_set if l.startswith("source:")),
        "manual",
    )
    severity = next(
        (l.split(":", 1)[1] for l in label_set if l.startswith("severity:")),
        None,
    )

    _upsert_issue(db, issue, complexity, source, severity)
    insert_issue_created(
        db,
        issue_id=issue.number,
        idempotency_key=f"issue-created-{issue.number}",
    )

    if complexity == "ambiguous":
        gh.add_label(issue.number, "needs-human-scoping")
        insert_session_declined(
            db,
            issue_id=issue.number,
            idempotency_key=f"session-declined-{issue.number}",
        )
        logger.info("orchestrator.issue_declined", extra={"issue_id": issue.number})
        return

    prompt = definite_prompt(issue) if complexity == "definite" else semi_definite_prompt(issue)
    repo_url = f"https://github.com/{cfg.github_repo}"

    try:
        session_id = devin.create_session(prompt, repo_url, issue.number)
    except DevinAPIError as exc:
        insert_session_start_failed(
            db,
            issue_id=issue.number,
            idempotency_key=f"session-start-failed-{issue.number}-{utcnow_iso()}",
            payload={"error": str(exc)},
        )
        logger.error("orchestrator.session_start_failed", extra={"issue_id": issue.number, "error": str(exc)})
        return

    with db:
        db.execute(
            "INSERT INTO sessions (session_id, issue_id, status, created_at) VALUES (?, ?, 'running', ?)",
            (session_id, issue.number, utcnow_iso()),
        )
    insert_session_started(
        db,
        issue_id=issue.number,
        session_id=session_id,
        idempotency_key=f"session-started-{session_id}",
    )
    logger.info("orchestrator.session_started", extra={"issue_id": issue.number, "session_id": session_id})
