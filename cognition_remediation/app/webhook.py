"""FastAPI router for GitHub webhook events.

Only registered if GITHUB_WEBHOOK_SECRET is set — otherwise the poller
handles issue discovery. Responds 200 immediately; all Devin work happens
in BackgroundTasks so the webhook round-trip is never blocked.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.db import utcnow_iso
from app.events import insert_issue_reopened, insert_pr_opened
from app.github_client import Issue
from app.orchestrator import handle_issue
from app.shared.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict:
    cfg = request.app.state.cfg
    db = request.app.state.db
    devin = request.app.state.devin
    gh = request.app.state.gh

    body = await request.body()

    if cfg.github_webhook_secret:
        if not _verify_signature(body, x_hub_signature_256, cfg.github_webhook_secret):
            logger.warning("webhook.invalid_signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

    payload = json.loads(body)
    action = payload.get("action", "")

    if x_github_event == "issues":
        if action == "opened":
            labels = [lb["name"] for lb in payload["issue"].get("labels", [])]
            if "auto-remediate" in labels:
                issue = Issue(
                    number=payload["issue"]["number"],
                    title=payload["issue"]["title"],
                    labels=labels,
                    body=payload["issue"].get("body") or "",
                )
                background_tasks.add_task(handle_issue, issue, db, devin, gh, cfg)
                logger.info("webhook.issue_dispatched", extra={"issue_id": issue.number})

        elif action == "reopened":
            issue_id = payload["issue"]["number"]
            now = utcnow_iso()
            insert_issue_reopened(
                db,
                issue_id=issue_id,
                idempotency_key=f"issue-reopened-{issue_id}-{now}",
            )
            with db:
                db.execute(
                    "UPDATE issues SET reopened_at = ? WHERE issue_id = ?",
                    (now, issue_id),
                )

    elif x_github_event == "pull_request" and action == "opened":
        pr_number = payload["pull_request"]["number"]
        session_row = db.execute(
            "SELECT session_id, issue_id FROM sessions"
            " WHERE status = 'running' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if session_row:
            insert_pr_opened(
                db,
                issue_id=session_row["issue_id"],
                session_id=session_row["session_id"],
                pr_number=pr_number,
                idempotency_key=f"pr-opened-{pr_number}",
            )
            with db:
                db.execute(
                    "UPDATE sessions SET pr_number = ? WHERE session_id = ?",
                    (pr_number, session_row["session_id"]),
                )

    return {"ok": True}
