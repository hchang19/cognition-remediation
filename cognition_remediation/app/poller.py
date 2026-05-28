"""Background polling thread.

Two periodic loops:
  - Session poller (every 30s): fetches Devin session status, writes terminal events
  - PR poller (every 5min): checks for human commits and CI run outcomes

If GITHUB_WEBHOOK_SECRET is unset, a third loop runs every 60s to pick up
new auto-remediate issues via the GitHub API (polling fallback).

Resilient: any per-item exception is caught and logged so one bad session
never halts the whole loop. Restarts cleanly from SQLite state after crash.
"""

from __future__ import annotations

import sqlite3
import time

from app.db import utcnow_iso
from app.devin_client import DevinClient, DevinAPIError
from app.events import (
    insert_pr_ci_completed,
    insert_pr_human_commit,
    insert_session_blocked,
    insert_session_completed,
    insert_session_failed,
)
from app.github_client import GitHubClient
from app.orchestrator import handle_issue
from app.shared.config import Config
from app.shared.logger import get_logger

logger = get_logger(__name__)

SESSION_POLL_INTERVAL = 30
PR_POLL_INTERVAL = 300
ISSUE_POLL_INTERVAL = 60


def _extract_pr_number(pr_url: str | None) -> int | None:
    if not pr_url:
        return None
    try:
        return int(pr_url.rstrip("/").split("/")[-1])
    except (ValueError, AttributeError):
        return None


def _poll_sessions(db: sqlite3.Connection, devin: DevinClient) -> None:
    rows = db.execute(
        "SELECT session_id, issue_id, status FROM sessions WHERE status = 'running'"
    ).fetchall()

    for row in rows:
        session_id, issue_id, current_status = row["session_id"], row["issue_id"], row["status"]
        try:
            resp = devin.get_session(session_id)
        except DevinAPIError as exc:
            logger.warning("poller.session_fetch_failed", extra={"session_id": session_id, "error": str(exc)})
            continue

        if resp.status == current_status:
            continue

        idem = f"session-{resp.status}-{session_id}"
        if resp.status == "completed":
            pr_num = _extract_pr_number(resp.pr_url)
            insert_session_completed(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem, pr_number=pr_num)
        elif resp.status == "failed":
            insert_session_failed(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem)
        elif resp.status == "blocked":
            insert_session_blocked(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem)

        updates: dict = {"status": resp.status, "cost_usd": resp.cost_usd, "session_url": resp.session_url}
        if resp.status in ("completed", "failed", "blocked"):
            updates["completed_at"] = utcnow_iso()
        if resp.pr_url:
            updates["pr_number"] = _extract_pr_number(resp.pr_url)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with db:
            db.execute(
                f"UPDATE sessions SET {set_clause} WHERE session_id = ?",
                (*updates.values(), session_id),
            )
        logger.info("poller.session_updated", extra={"session_id": session_id, "new_status": resp.status})


def _poll_prs(db: sqlite3.Connection, gh: GitHubClient) -> None:
    rows = db.execute(
        "SELECT session_id, issue_id, pr_number, human_intervened, ci_first_pass"
        " FROM sessions WHERE status = 'completed' AND pr_number IS NOT NULL"
    ).fetchall()

    for row in rows:
        session_id = row["session_id"]
        issue_id = row["issue_id"]
        pr_number = row["pr_number"]

        try:
            commits = gh.get_pr_commits(pr_number)
        except Exception as exc:
            logger.warning("poller.pr_commits_failed", extra={"pr_number": pr_number, "error": str(exc)})
            continue

        if not row["human_intervened"]:
            non_devin = [c for c in commits if "devin" not in c.author.lower()]
            if non_devin:
                insert_pr_human_commit(
                    db,
                    pr_number=pr_number,
                    idempotency_key=f"pr-human-commit-{pr_number}",
                    issue_id=issue_id,
                    session_id=session_id,
                )
                with db:
                    db.execute(
                        "UPDATE sessions SET human_intervened = 1 WHERE session_id = ?",
                        (session_id,),
                    )

        if row["ci_first_pass"] is None:
            try:
                ci = gh.get_latest_ci_run(pr_number)
            except Exception as exc:
                logger.warning("poller.ci_fetch_failed", extra={"pr_number": pr_number, "error": str(exc)})
                continue

            if ci and ci.status == "completed":
                insert_pr_ci_completed(
                    db,
                    pr_number=pr_number,
                    idempotency_key=f"pr-ci-completed-{pr_number}",
                    issue_id=issue_id,
                    session_id=session_id,
                    payload={"conclusion": ci.conclusion},
                )
                with db:
                    db.execute(
                        "UPDATE sessions SET ci_first_pass = ? WHERE session_id = ?",
                        (1 if ci.conclusion == "success" else 0, session_id),
                    )


def _poll_issues(db: sqlite3.Connection, gh: GitHubClient, devin: DevinClient, cfg: Config) -> None:
    issues = gh.get_open_issues("auto-remediate")
    for issue in issues:
        try:
            handle_issue(issue, db, devin, gh, cfg)
        except Exception as exc:
            logger.error("poller.handle_issue_error", extra={"issue_id": issue.number, "error": str(exc)})


def start_poller(db: sqlite3.Connection, devin: DevinClient, gh: GitHubClient, cfg: Config) -> None:
    logger.info("poller.started")
    session_last = pr_last = issue_last = 0.0

    while True:
        now = time.monotonic()

        if now - session_last >= SESSION_POLL_INTERVAL:
            try:
                _poll_sessions(db, devin)
            except Exception as exc:
                logger.error("poller.session_loop_error", extra={"error": str(exc)})
            session_last = now

        if now - pr_last >= PR_POLL_INTERVAL:
            try:
                _poll_prs(db, gh)
            except Exception as exc:
                logger.error("poller.pr_loop_error", extra={"error": str(exc)})
            pr_last = now

        if not cfg.github_webhook_secret and now - issue_last >= ISSUE_POLL_INTERVAL:
            try:
                _poll_issues(db, gh, devin, cfg)
            except Exception as exc:
                logger.error("poller.issue_loop_error", extra={"error": str(exc)})
            issue_last = now

        time.sleep(5)
