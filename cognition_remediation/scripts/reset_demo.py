"""Reset the demo: close all `auto-remediate` issues and wipe the SQLite DB.

The only script outside the normal app lifecycle that touches SQLite.

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

from app.shared.config import load_config  # noqa: E402
from app.shared.github_session import github_session  # noqa: E402
from app.shared.logger import get_logger  # noqa: E402
from app.shared.retry import with_retry  # noqa: E402

logger = get_logger(__name__)

GITHUB_API = "https://api.github.com"
TABLES_TO_WIPE = ("events", "sessions", "issues")


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


def close_issue(session: requests.Session, repo: str, number: int) -> None:
    url = f"{GITHUB_API}/repos/{repo}/issues/{number}"
    _patch(session, url, json={"state": "closed"})


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

    issues = fetch_auto_remediate_issues(session, cfg.github_repo)
    logger.info("reset.found", extra={"count": len(issues)})

    closed = 0
    for issue in issues:
        number = issue.get("number")
        if number is None:
            continue
        close_issue(session, cfg.github_repo, number)
        logger.info("issue.closed", extra={"number": number, "title": issue.get("title")})
        closed += 1

    wipe_sqlite(cfg.db_path)

    logger.info("reset.done", extra={"closed": closed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
