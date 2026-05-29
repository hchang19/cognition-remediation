"""Typed event-insert helpers.

Every state change in the system is recorded as an append-only row in
``events``. All inserts use ``INSERT OR IGNORE`` on ``idempotency_key`` so
duplicate deliveries (webhook retries, poll-after-webhook, etc.) are silent
no-ops.

Use :func:`insert_event` directly only if you need to insert an event type that
doesn't yet have a typed wrapper. Otherwise prefer the wrappers below — they
document each event's expected columns and make grep-ability sane.

Event taxonomy (matches ``docs/stage-2-db.md``):

    issue.created       issue.closed        issue.reopened
    session.started     session.completed   session.failed
    session.pending     session.blocked     session.declined    session.start_failed
    pr.opened           pr.human_commit     pr.ci_completed
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import utcnow_iso
from app.shared.logger import get_logger

logger = get_logger(__name__)


# Event type constants — single source of truth used by wrappers.
EVENT_ISSUE_CREATED = "issue.created"
EVENT_ISSUE_CLOSED = "issue.closed"
EVENT_ISSUE_REOPENED = "issue.reopened"
EVENT_SESSION_STARTED = "session.started"
EVENT_SESSION_COMPLETED = "session.completed"
EVENT_SESSION_FAILED = "session.failed"
EVENT_SESSION_PENDING = "session.pending"
EVENT_SESSION_BLOCKED = "session.blocked"
EVENT_SESSION_DECLINED = "session.declined"
EVENT_SESSION_START_FAILED = "session.start_failed"
EVENT_PR_OPENED = "pr.opened"
EVENT_PR_HUMAN_COMMIT = "pr.human_commit"
EVENT_PR_CI_COMPLETED = "pr.ci_completed"


def insert_event(
    db: sqlite3.Connection,
    event_type: str,
    idempotency_key: str,
    *,
    issue_id: int | None = None,
    session_id: str | None = None,
    pr_number: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert a single event row.

    Silently ignores duplicates (same ``idempotency_key``). ``payload`` is
    JSON-serialized with ``default=str`` so non-JSON-native values (datetime,
    Decimal, Path, UUID) are best-effort stringified instead of raising
    TypeError. Pass ``None`` to store NULL.
    """
    payload_json = json.dumps(payload, default=str) if payload is not None else None
    with db:
        db.execute(
            """
            INSERT OR IGNORE INTO events (
                timestamp, event_type, issue_id, session_id, pr_number,
                payload, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utcnow_iso(),
                event_type,
                issue_id,
                session_id,
                pr_number,
                payload_json,
                idempotency_key,
            ),
        )
    logger.info(
        "event_inserted",
        extra={
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "issue_id": issue_id,
            "session_id": session_id,
            "pr_number": pr_number,
        },
    )


# ---------------------------------------------------------------------------
# Issue events
# ---------------------------------------------------------------------------


def insert_issue_created(
    db: sqlite3.Connection,
    issue_id: int,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_ISSUE_CREATED,
        idempotency_key,
        issue_id=issue_id,
        payload=payload,
    )


def insert_issue_closed(
    db: sqlite3.Connection,
    issue_id: int,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_ISSUE_CLOSED,
        idempotency_key,
        issue_id=issue_id,
        payload=payload,
    )


def insert_issue_reopened(
    db: sqlite3.Connection,
    issue_id: int,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_ISSUE_REOPENED,
        idempotency_key,
        issue_id=issue_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Session lifecycle events
# ---------------------------------------------------------------------------


def insert_session_started(
    db: sqlite3.Connection,
    issue_id: int,
    session_id: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_SESSION_STARTED,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        payload=payload,
    )


def insert_session_completed(
    db: sqlite3.Connection,
    issue_id: int,
    session_id: str,
    idempotency_key: str,
    pr_number: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_SESSION_COMPLETED,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        pr_number=pr_number,
        payload=payload,
    )


def insert_session_failed(
    db: sqlite3.Connection,
    issue_id: int,
    session_id: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_SESSION_FAILED,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        payload=payload,
    )


def insert_session_pending(
    db: sqlite3.Connection,
    issue_id: int,
    session_id: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_SESSION_PENDING,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        payload=payload,
    )


def insert_session_blocked(
    db: sqlite3.Connection,
    issue_id: int,
    session_id: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_SESSION_BLOCKED,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        payload=payload,
    )


def insert_session_declined(
    db: sqlite3.Connection,
    issue_id: int,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Declined issues never get a sessions row — only this event."""
    insert_event(
        db,
        EVENT_SESSION_DECLINED,
        idempotency_key,
        issue_id=issue_id,
        payload=payload,
    )


def insert_session_start_failed(
    db: sqlite3.Connection,
    issue_id: int,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Devin API call to create the session failed — no session_id yet."""
    insert_event(
        db,
        EVENT_SESSION_START_FAILED,
        idempotency_key,
        issue_id=issue_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# PR events
# ---------------------------------------------------------------------------


def insert_pr_opened(
    db: sqlite3.Connection,
    issue_id: int,
    session_id: str,
    pr_number: int,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_PR_OPENED,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        pr_number=pr_number,
        payload=payload,
    )


def insert_pr_human_commit(
    db: sqlite3.Connection,
    pr_number: int,
    idempotency_key: str,
    issue_id: int | None = None,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_PR_HUMAN_COMMIT,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        pr_number=pr_number,
        payload=payload,
    )


def insert_pr_ci_completed(
    db: sqlite3.Connection,
    pr_number: int,
    idempotency_key: str,
    issue_id: int | None = None,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    insert_event(
        db,
        EVENT_PR_CI_COMPLETED,
        idempotency_key,
        issue_id=issue_id,
        session_id=session_id,
        pr_number=pr_number,
        payload=payload,
    )
