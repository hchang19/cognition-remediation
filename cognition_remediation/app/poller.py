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
from datetime import datetime, timezone

from app.db import utcnow_iso
from app.devin_client import DevinClient, DevinAPIError
from app.events import (
    insert_pr_ci_completed,
    insert_pr_human_commit,
    insert_session_blocked,
    insert_session_completed,
    insert_session_failed,
    insert_session_pending,
)
from app.github_client import GitHubClient
from app.orchestrator import handle_issue
from app.shared.config import Config
from app.shared.logger import get_logger

logger = get_logger(__name__)

SESSION_POLL_INTERVAL = 30
PR_POLL_INTERVAL = 300
ISSUE_POLL_INTERVAL = 60

# Labels applied when a session can't finish.
_LABEL_INCOMPLETE = "devin-incomplete"   # cost cap or time limit hit
_LABEL_FAILED = "devin-failed"           # Devin reported failure
_LABEL_BLOCKED = "devin-blocked"         # Devin waiting on human input


def _extract_pr_number(pr_url: str | None) -> int | None:
    if not pr_url:
        return None
    try:
        return int(pr_url.rstrip("/").split("/")[-1])
    except (ValueError, AttributeError):
        return None


def _post_pending_comment(
    gh: GitHubClient,
    issue_number: int,
    session_id: str,
    session_url: str | None,
    pr_number: int | None,
    status_detail: str | None,
) -> None:
    """Post a comment on the PR (or issue if no PR yet) when Devin is waiting for input."""
    target = pr_number or issue_number
    reason_map = {
        "waiting_for_user": "Devin needs your input to continue",
        "waiting_for_approval": "Devin is waiting for action approval (safe mode)",
        "user_request": "session was suspended at your request",
        "inactivity": "session was suspended due to inactivity",
    }
    reason = reason_map.get(status_detail or "", "Devin is waiting for human input")
    url_str = session_url or f"https://app.devin.ai/sessions/{session_id}"
    body = (
        f"## Devin session is paused — {reason}\n\n"
        f"**Session:** {url_str}  \n"
        f"**Session ID:** `{session_id}`\n\n"
        f"Please visit the session link above to respond or approve the pending action.\n\n"
        f"---\n"
        f"_Posted automatically by the remediation orchestrator._"
    )
    gh.post_comment(issue_number=target, body=body)


def _post_incomplete_comment(
    gh: GitHubClient,
    issue_number: int,
    reason: str,
    session_url: str | None,
    cost_usd: float | None,
    output: str | None,
) -> None:
    """Post a structured comment on the GitHub issue explaining why Devin stopped."""
    cost_str = f"${cost_usd:.2f}" if cost_usd is not None else "unknown"
    url_str = session_url or "n/a"
    progress = output.strip() if output and output.strip() else "_No output captured from this session._"
    body = (
        f"## Devin session could not complete — {reason}\n\n"
        f"**Session:** {url_str}  \n"
        f"**Cost so far:** {cost_str}\n\n"
        f"### What was attempted\n\n"
        f"{progress}\n\n"
        f"---\n"
        f"_This issue has been labelled for human follow-up._"
    )
    gh.post_comment(issue_number=issue_number, body=body)


def _handle_incomplete_session(
    db: sqlite3.Connection,
    gh: GitHubClient,
    devin: DevinClient,
    session_id: str,
    issue_id: int,
    reason: str,
    label: str,
    resp_status: str,
    cost_usd: float | None,
    session_url: str | None,
    output: str | None,
    terminate: bool = False,
) -> None:
    """Terminate (optionally), post a progress comment, and update the issue label."""
    if terminate:
        try:
            devin.terminate_session(session_id)
            logger.info("poller.session_terminated", extra={"session_id": session_id, "reason": reason})
        except Exception as exc:
            logger.warning("poller.terminate_failed", extra={"session_id": session_id, "error": str(exc)})

    try:
        _post_incomplete_comment(gh, issue_id, reason, session_url, cost_usd, output)
        gh.add_label(issue_number=issue_id, label=label)
    except Exception as exc:
        logger.warning("poller.incomplete_comment_failed", extra={"issue_id": issue_id, "error": str(exc)})

    final_status = "failed" if terminate else resp_status
    idem = f"session-{final_status}-{session_id}"
    insert_session_failed(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem)
    now = utcnow_iso()
    with db:
        db.execute(
            "UPDATE sessions SET status=?, cost_usd=?, session_url=?, completed_at=? WHERE session_id=?",
            (final_status, cost_usd, session_url, now, session_id),
        )
    logger.info("poller.session_incomplete", extra={"session_id": session_id, "reason": reason, "label": label})


def _poll_sessions(
    db: sqlite3.Connection,
    devin: DevinClient,
    gh: GitHubClient | None = None,
    cfg: Config | None = None,
) -> None:
    rows = db.execute(
        "SELECT session_id, issue_id, status, created_at, pr_number"
        " FROM sessions WHERE status IN ('running', 'pending')"
    ).fetchall()

    for row in rows:
        session_id = row["session_id"]
        issue_id = row["issue_id"]
        current_status = row["status"]
        created_at_str = row["created_at"]
        pr_number = row["pr_number"]

        try:
            resp = devin.get_session(session_id)
        except DevinAPIError as exc:
            logger.warning("poller.session_fetch_failed", extra={"session_id": session_id, "error": str(exc)})
            continue

        # --- Cost cap check ---
        if (
            gh and cfg
            and cfg.devin_session_cost_limit_usd is not None
            and resp.cost_usd is not None
            and resp.cost_usd >= cfg.devin_session_cost_limit_usd
        ):
            _handle_incomplete_session(
                db, gh, devin, session_id, issue_id,
                reason=f"cost limit ${cfg.devin_session_cost_limit_usd:.2f} reached (${resp.cost_usd:.2f} spent)",
                label=_LABEL_INCOMPLETE,
                resp_status=resp.status,
                cost_usd=resp.cost_usd,
                session_url=resp.session_url,
                output=resp.output,
                terminate=True,
            )
            continue

        # --- Time limit check ---
        if gh and cfg and cfg.devin_session_time_limit_minutes is not None and created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                age_minutes = (datetime.now(timezone.utc) - created_at).total_seconds() / 60
                if age_minutes >= cfg.devin_session_time_limit_minutes:
                    _handle_incomplete_session(
                        db, gh, devin, session_id, issue_id,
                        reason=f"time limit {cfg.devin_session_time_limit_minutes}min reached ({int(age_minutes)}min elapsed)",
                        label=_LABEL_INCOMPLETE,
                        resp_status=resp.status,
                        cost_usd=resp.cost_usd,
                        session_url=resp.session_url,
                        output=resp.output,
                        terminate=True,
                    )
                    continue
            except Exception as exc:
                logger.warning("poller.time_limit_check_failed", extra={"session_id": session_id, "error": str(exc)})

        if resp.status == current_status:
            continue

        # --- Status transition handling ---
        idem = f"session-{resp.status}-{session_id}"
        if resp.status == "completed":
            pr_num = _extract_pr_number(resp.pr_url)
            insert_session_completed(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem, pr_number=pr_num)
        elif resp.status == "failed":
            insert_session_failed(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem)
            if gh:
                try:
                    _post_incomplete_comment(gh, issue_id, "session failed", resp.session_url, resp.cost_usd, resp.output)
                    gh.add_label(issue_number=issue_id, label=_LABEL_FAILED)
                except Exception as exc:
                    logger.warning("poller.failed_comment_error", extra={"issue_id": issue_id, "error": str(exc)})
        elif resp.status == "blocked":
            insert_session_blocked(db, issue_id=issue_id, session_id=session_id, idempotency_key=idem)
            if gh:
                try:
                    _post_incomplete_comment(gh, issue_id, "session hit resource limit", resp.session_url, resp.cost_usd, resp.output)
                    gh.add_label(issue_number=issue_id, label=_LABEL_BLOCKED)
                except Exception as exc:
                    logger.warning("poller.blocked_comment_error", extra={"issue_id": issue_id, "error": str(exc)})
        elif resp.status == "pending":
            # Non-terminal: Devin is waiting for human input or approval.
            # Use idempotency key that tracks the specific detail so a
            # pending→running→pending cycle records both occurrences.
            idem = f"session-pending-{session_id}-{resp.status_detail or 'unknown'}"
            insert_session_pending(
                db, issue_id=issue_id, session_id=session_id, idempotency_key=idem,
                payload={"status_detail": resp.status_detail},
            )
            if gh:
                try:
                    _post_pending_comment(gh, issue_id, session_id, resp.session_url, pr_number, resp.status_detail)
                except Exception as exc:
                    logger.warning("poller.pending_comment_error", extra={"issue_id": issue_id, "error": str(exc)})
        # running (from pending→running resume): no event, just update DB below

        updates: dict = {
            "status": resp.status,
            "status_detail": resp.status_detail,
            "cost_usd": resp.cost_usd,
            "session_url": resp.session_url,
        }
        # Only terminal statuses get a completed_at timestamp.
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
                _poll_sessions(db, devin, gh=gh, cfg=cfg)
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
