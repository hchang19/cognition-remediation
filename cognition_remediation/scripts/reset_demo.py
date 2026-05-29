"""Hard reset: terminate all Devin sessions, close GitHub issues and PRs, wipe SQLite.

Execution order matters — session IDs are read from the DB before it is wiped:

  1. Terminate running/pending Devin sessions (requires DEVIN_API_KEY + DEVIN_ORG_ID)
  2. Close open `fix/` PRs and delete their branches
  3. Close open `auto-remediate` issues
  4. Wipe all SQLite tables (events, sessions, issues)

Safe to run at any point in the demo lifecycle. Idempotent: skips resources
that are already closed/terminated.

Run:
    python3 -m scripts.reset_demo
or
    python3 cognition_remediation/scripts/reset_demo.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

# Allow running as a top-level script.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.devin_client import DevinClient, DevinAPIError  # noqa: E402
from app.shared.config import load_config  # noqa: E402
from app.shared.github_session import github_session  # noqa: E402
from app.shared.logger import get_logger  # noqa: E402
from app.shared.retry import with_retry  # noqa: E402

logger = get_logger(__name__)

GITHUB_API = "https://api.github.com"
TABLES_TO_WIPE = ("events", "sessions", "reviewer_sessions", "issues")


@with_retry()
def _get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = session.get(url, **kwargs)
    response.raise_for_status()
    return response


@with_retry()
def _patch(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = session.patch(url, **kwargs)
    response.raise_for_status()
    return response


@with_retry()
def _delete(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = session.delete(url, **kwargs)
    response.raise_for_status()
    return response


def fetch_auto_remediate_issues(session: requests.Session, repo: str) -> list[dict[str, Any]]:
    """Return all open issues labelled `auto-remediate` (open only — closed ones don't need closing)."""
    issues: list[dict[str, Any]] = []
    url = f"{GITHUB_API}/repos/{repo}/issues"
    params = {"state": "open", "labels": "auto-remediate", "per_page": 100, "page": 1}
    while True:
        response = _get(session, url, params=params)
        batch = response.json()
        if not batch:
            break
        for item in batch:
            if "pull_request" in item:
                continue
            issues.append(item)
        if len(batch) < params["per_page"]:
            break
        params["page"] += 1
    return issues


def fetch_open_prs(session: requests.Session, repo: str) -> list[dict[str, Any]]:
    """Return all open pull requests with a fix/ branch (Devin-created PRs)."""
    prs: list[dict[str, Any]] = []
    url = f"{GITHUB_API}/repos/{repo}/pulls"
    params: dict[str, Any] = {"state": "open", "per_page": 100, "page": 1}
    while True:
        response = _get(session, url, params=params)
        batch = response.json()
        if not batch:
            break
        for item in batch:
            if item.get("head", {}).get("ref", "").startswith("fix/"):
                prs.append(item)
        if len(batch) < params["per_page"]:
            break
        params["page"] += 1
    return prs


def close_issue(session: requests.Session, repo: str, number: int) -> None:
    url = f"{GITHUB_API}/repos/{repo}/issues/{number}"
    _patch(session, url, json={"state": "closed"})


def close_pr(session: requests.Session, repo: str, number: int) -> None:
    url = f"{GITHUB_API}/repos/{repo}/pulls/{number}"
    _patch(session, url, json={"state": "closed"})


def delete_branch(session: requests.Session, repo: str, branch: str) -> None:
    url = f"{GITHUB_API}/repos/{repo}/git/refs/heads/{branch}"
    _delete(session, url)


def fetch_active_session_ids(db_path: str) -> list[str]:
    """Return session IDs of all running/pending sessions in the DB.

    Must be called BEFORE wipe_sqlite — the DB is the only source of truth
    for which Devin sessions are currently active.
    """
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    try:
        try:
            rows = conn.execute(
                "SELECT session_id FROM sessions WHERE status IN ('running', 'pending')"
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


def terminate_devin_sessions(devin: DevinClient, session_ids: list[str]) -> tuple[int, int]:
    """Terminate each active Devin session. Returns (terminated, failed) counts."""
    terminated = failed = 0
    for sid in session_ids:
        try:
            devin.terminate_session(sid)
            logger.info("devin.session_terminated", extra={"session_id": sid})
            terminated += 1
        except DevinAPIError as exc:
            logger.warning("devin.terminate_failed", extra={"session_id": sid, "error": str(exc)})
            failed += 1
    return terminated, failed


def wipe_sqlite(db_path: str) -> None:
    """DELETE FROM each known table. Schema may not yet exist — wrap in try/except per table."""
    path = Path(db_path)
    if not path.exists():
        logger.info("sqlite.skip_missing", extra={"db_path": str(path)})
        return

    conn = sqlite3.connect(str(path))
    try:
        for table in TABLES_TO_WIPE:
            try:
                conn.execute(f"DELETE FROM {table}")
                logger.info("sqlite.wiped", extra={"table": table})
            except sqlite3.OperationalError as exc:
                # Table doesn't exist yet (stage 2 not built or fresh DB).
                logger.info("sqlite.table_missing", extra={"table": table, "error": str(exc)})
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    cfg = load_config()
    session = github_session(cfg.github_token)

    logger.info("reset.start", extra={"repo": cfg.github_repo, "db_path": cfg.db_path})

    # Step 1: Terminate active Devin sessions BEFORE wiping the DB.
    # The DB is the only record of which session IDs are running.
    active_ids = fetch_active_session_ids(cfg.db_path)
    logger.info("reset.devin_sessions_found", extra={"count": len(active_ids)})
    terminated = failed_terminate = 0
    if active_ids:
        devin = DevinClient(api_key=cfg.devin_api_key, org_id=cfg.devin_org_id)
        terminated, failed_terminate = terminate_devin_sessions(devin, active_ids)

    # Step 2: Close open PRs on fix/ branches and delete those branches.
    open_prs = fetch_open_prs(session, cfg.github_repo)
    logger.info("reset.prs_found", extra={"count": len(open_prs)})
    closed_prs = 0
    deleted_branches = 0
    for pr in open_prs:
        number = pr.get("number")
        branch = pr.get("head", {}).get("ref", "")
        if number is None:
            continue
        try:
            close_pr(session, cfg.github_repo, number)
            logger.info("pr.closed", extra={"number": number, "branch": branch})
            closed_prs += 1
        except Exception as exc:
            logger.warning("pr.close_failed", extra={"number": number, "error": str(exc)})
        if branch:
            try:
                delete_branch(session, cfg.github_repo, branch)
                logger.info("branch.deleted", extra={"branch": branch})
                deleted_branches += 1
            except Exception as exc:
                logger.warning("branch.delete_failed", extra={"branch": branch, "error": str(exc)})

    # Step 4: Close open auto-remediate issues.
    issues = fetch_auto_remediate_issues(session, cfg.github_repo)
    logger.info("reset.issues_found", extra={"count": len(issues)})
    closed_issues = 0
    for issue in issues:
        number = issue.get("number")
        if number is None:
            continue
        try:
            close_issue(session, cfg.github_repo, number)
            logger.info("issue.closed", extra={"number": number, "title": issue.get("title")})
            closed_issues += 1
        except Exception as exc:
            logger.warning("issue.close_failed", extra={"number": number, "error": str(exc)})

    # Step 5: Wipe the SQLite DB.
    wipe_sqlite(cfg.db_path)

    logger.info(
        "reset.done",
        extra={
            "devin_terminated": terminated,
            "devin_terminate_failed": failed_terminate,
            "closed_prs": closed_prs,
            "deleted_branches": deleted_branches,
            "closed_issues": closed_issues,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
