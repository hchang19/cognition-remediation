"""Thin wrapper around the Devin REST API (v3).

No business logic — callers (Stage 4 orchestrator) decide what to do with
the results. All HTTP calls use ``with_retry`` for 5xx / network resilience.
Raises ``DevinAPIError`` after retries are exhausted.

Status normalization
--------------------
The v3 API returns its own status vocabulary. ``get_session()`` normalizes
those to our internal states before returning so callers never see raw API
values:

  Devin v3 status   + status_detail              → internal status
  ─────────────────────────────────────────────────────────────────
  exit              (any)                         → completed   (terminal)
  error             (any)                         → failed      (terminal)
  suspended         usage/credit/quota limits     → blocked     (terminal)
  suspended         user_request / inactivity     → pending     (non-terminal)
  running           waiting_for_user/approval     → pending     (non-terminal)
  running           finished                      → completed   (terminal)
  running           working / None                → running     (non-terminal)
  new / claimed / resuming  (any)                 → running     (non-terminal)
"""

from __future__ import annotations

import requests
from dataclasses import dataclass, field

from app.shared.logger import get_logger
from app.shared.retry import with_retry

logger = get_logger(__name__)

_API_ROOT = "https://api.devin.ai/v3"

# suspended status_details that are resumable (human can respond → pending)
_SUSPENDED_RESUMABLE = {"user_request", "inactivity"}

# suspended status_details that hit resource limits (terminal → blocked)
_SUSPENDED_TERMINAL = {
    "usage_limit_exceeded", "out_of_credits", "out_of_quota",
    "no_quota_allocation", "payment_declined",
    "org_usage_limit_exceeded", "total_session_limit_exceeded", "error",
}


def _normalize_status(api_status: str, status_detail: str | None) -> str:
    """Map Devin v3 API status → our internal session status."""
    if api_status == "exit":
        return "completed"
    if api_status == "error":
        return "failed"
    if api_status == "running":
        if status_detail in ("waiting_for_user", "waiting_for_approval"):
            return "pending"
        if status_detail == "finished":
            return "completed"
        return "running"
    if api_status == "suspended":
        if status_detail in _SUSPENDED_RESUMABLE:
            return "pending"
        return "blocked"
    # new, claimed, resuming → treat as running
    return "running"


class DevinAPIError(Exception):
    """Raised when the Devin API fails after all retries."""


@dataclass
class SessionResponse:
    session_id: str
    status: str            # internal: "running" | "pending" | "completed" | "failed" | "blocked"
    cost_usd: float | None  # populated from acus_consumed (ACU proxy; no USD conversion available)
    session_url: str | None
    pr_url: str | None
    output: str | None
    status_detail: str | None = field(default=None)  # raw v3 status_detail for logging/display


class DevinClient:
    def __init__(self, api_key: str, org_id: str) -> None:
        self._base = f"{_API_ROOT}/organizations/{org_id}"
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    @with_retry()
    def _post(self, url: str, **kwargs) -> requests.Response:
        r = self._session.post(url, **kwargs)
        r.raise_for_status()
        return r

    @with_retry()
    def _get(self, url: str, **kwargs) -> requests.Response:
        r = self._session.get(url, **kwargs)
        r.raise_for_status()
        return r

    def create_session(self, prompt: str, repo_url: str, issue_id: int) -> str:
        """Start a Devin session. Returns session_id."""
        try:
            r = self._post(
                f"{self._base}/sessions",
                json={
                    "prompt": prompt,
                    "repo_url": repo_url,
                    "metadata": {"issue_id": issue_id},
                },
            )
        except requests.HTTPError as exc:
            raise DevinAPIError(f"{exc} — body: {exc.response.text}") from exc
        except requests.ConnectionError as exc:
            raise DevinAPIError(str(exc)) from exc
        session_id = r.json()["session_id"]
        logger.info(
            "devin.session_created",
            extra={"session_id": session_id, "issue_id": issue_id},
        )
        return session_id

    def get_session(self, session_id: str) -> SessionResponse:
        """Fetch current session state, normalized to internal status vocabulary."""
        try:
            r = self._get(f"{self._base}/sessions/{session_id}")
        except (requests.ConnectionError, requests.HTTPError) as exc:
            raise DevinAPIError(str(exc)) from exc
        data = r.json()

        api_status = data.get("status", "")
        status_detail = data.get("status_detail")
        internal_status = _normalize_status(api_status, status_detail)

        # pr_url: v3 returns pull_requests[] array; fall back to legacy pr_url field
        pull_requests = data.get("pull_requests") or []
        pr_url = pull_requests[0].get("url") if pull_requests else data.get("pr_url")

        # output: v3 returns messages[]; extract last non-user message text
        messages = data.get("messages") or []
        output = None
        for msg in reversed(messages):
            if msg.get("type") != "user" and msg.get("message"):
                output = msg["message"]
                break
        if output is None:
            output = data.get("output")

        # cost: v3 uses acus_consumed (Agent Compute Units); no USD conversion available
        cost_usd = data.get("acus_consumed") or data.get("cost_usd")

        response = SessionResponse(
            session_id=data["session_id"],
            status=internal_status,
            status_detail=status_detail,
            cost_usd=cost_usd,
            session_url=data.get("url") or data.get("session_url"),
            pr_url=pr_url,
            output=output,
        )
        logger.debug(
            "devin.session_polled",
            extra={
                "session_id": session_id,
                "api_status": api_status,
                "status_detail": status_detail,
                "internal_status": internal_status,
            },
        )
        return response

    def terminate_session(self, session_id: str) -> None:
        """Terminate an active session immediately."""
        try:
            r = self._session.delete(f"{self._base}/sessions/{session_id}")
            r.raise_for_status()
        except (requests.ConnectionError, requests.HTTPError) as exc:
            raise DevinAPIError(str(exc)) from exc
        logger.info("devin.session_terminated", extra={"session_id": session_id})
