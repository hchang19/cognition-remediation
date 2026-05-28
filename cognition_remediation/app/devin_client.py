"""Thin wrapper around the Devin REST API.

No business logic — callers (Stage 4 orchestrator) decide what to do with
the results. All HTTP calls use ``with_retry`` for 5xx / network resilience.
Raises ``DevinAPIError`` after retries are exhausted.
"""

from __future__ import annotations

import requests
from dataclasses import dataclass

from app.shared.logger import get_logger
from app.shared.retry import with_retry

logger = get_logger(__name__)

_BASE_URL = "https://api.cognition.ai/v1"


class DevinAPIError(Exception):
    """Raised when the Devin API fails after all retries."""


@dataclass
class SessionResponse:
    session_id: str
    status: str           # "running" | "completed" | "failed" | "blocked"
    cost_usd: float | None
    session_url: str | None
    pr_url: str | None
    output: str | None


class DevinClient:
    def __init__(self, api_key: str) -> None:
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
                f"{_BASE_URL}/sessions",
                json={
                    "prompt": prompt,
                    "repo_url": repo_url,
                    "metadata": {"issue_id": issue_id},
                },
            )
        except (requests.ConnectionError, requests.HTTPError) as exc:
            raise DevinAPIError(str(exc)) from exc
        session_id = r.json()["session_id"]
        logger.info(
            "devin.session_created",
            extra={"session_id": session_id, "issue_id": issue_id},
        )
        return session_id

    def get_session(self, session_id: str) -> SessionResponse:
        """Fetch current session state."""
        try:
            r = self._get(f"{_BASE_URL}/sessions/{session_id}")
        except (requests.ConnectionError, requests.HTTPError) as exc:
            raise DevinAPIError(str(exc)) from exc
        data = r.json()
        response = SessionResponse(
            session_id=data["session_id"],
            status=data["status"],
            cost_usd=data.get("cost_usd"),
            session_url=data.get("session_url"),
            pr_url=data.get("pr_url"),
            output=data.get("output"),
        )
        logger.debug(
            "devin.session_polled",
            extra={"session_id": session_id, "status": response.status},
        )
        return response
