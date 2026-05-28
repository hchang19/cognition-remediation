"""Seed the GitHub fork with the issue set defined in config/issues.yml.

Idempotent: each issue body carries a `<!-- cognition-meta ... -->` block
containing an `idempotency_key`. Before creating an issue we list open and
closed issues in the repo and skip any whose body already contains the key.

Run:
    python3 -m scripts.seed_issues
or
    python3 cognition_remediation/scripts/seed_issues.py

Reads GITHUB_TOKEN and GITHUB_REPO from the environment (see
`app/shared/config.py`). Does NOT touch SQLite — the orchestrator writes the
DB when the webhook fires.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable

# Allow running as a top-level script (python3 scripts/seed_issues.py) by
# putting the package root on sys.path. When imported as `scripts.seed_issues`
# from within `cognition_remediation/`, this is a no-op.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

import requests  # noqa: E402
import yaml  # noqa: E402

from app.shared.config import load_config  # noqa: E402
from app.shared.github_session import github_session  # noqa: E402
from app.shared.logger import get_logger  # noqa: E402
from app.shared.retry import with_retry  # noqa: E402

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = _PKG_ROOT / "config" / "issues.yml"
GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# HTTP wrappers (retry on 5xx / connection errors via with_retry)
# ---------------------------------------------------------------------------


@with_retry()
def _get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = session.get(url, **kwargs)
    response.raise_for_status()
    return response


@with_retry()
def _post(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = session.post(url, **kwargs)
    # 422 from labels (already exists) is handled by caller — don't raise here
    # when it's the expected duplicate-label condition.
    if response.status_code == 422:
        return response
    response.raise_for_status()
    return response


# ---------------------------------------------------------------------------
# Label management
# ---------------------------------------------------------------------------


def _collect_labels(issues: Iterable[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for issue in issues:
        for label in issue.get("labels", []) or []:
            seen.setdefault(str(label), None)
    return list(seen.keys())


def ensure_labels(session: requests.Session, repo: str, labels: Iterable[str]) -> None:
    """Create each label if missing. Ignores 422 (already-exists)."""
    url = f"{GITHUB_API}/repos/{repo}/labels"
    for label in labels:
        payload = {"name": label, "color": "ededed"}
        response = _post(session, url, json=payload)
        if response.status_code == 201:
            logger.info("label.created", extra={"label": label})
        elif response.status_code == 422:
            logger.info("label.exists", extra={"label": label})
        else:
            logger.warning(
                "label.unexpected_status",
                extra={"label": label, "status": response.status_code},
            )


# ---------------------------------------------------------------------------
# Idempotency: scan existing issues for the key
# ---------------------------------------------------------------------------


def fetch_existing_issue_bodies(session: requests.Session, repo: str) -> list[str]:
    """Return the body text of every open and closed issue in the repo.

    Uses /issues?state=all with pagination. Filters out pull requests (GitHub's
    issues endpoint returns PRs too — they carry a `pull_request` key).
    """
    bodies: list[str] = []
    url = f"{GITHUB_API}/repos/{repo}/issues"
    params = {"state": "all", "per_page": 100, "page": 1}
    while True:
        response = _get(session, url, params=params)
        batch = response.json()
        if not batch:
            break
        for item in batch:
            if "pull_request" in item:
                continue
            body = item.get("body") or ""
            bodies.append(body)
        if len(batch) < params["per_page"]:
            break
        params["page"] += 1
    return bodies


def _already_seeded(key: str, existing_bodies: list[str]) -> bool:
    return any(key in body for body in existing_bodies)


# ---------------------------------------------------------------------------
# Issue creation
# ---------------------------------------------------------------------------


def _extract_idempotency_key(body: str) -> str | None:
    """Pull the idempotency_key out of the cognition-meta block.

    Cheap string scan — avoids a full JSON parse so authors can use loose
    formatting in the YAML body.
    """
    marker = '"idempotency_key"'
    idx = body.find(marker)
    if idx == -1:
        return None
    rest = body[idx + len(marker):]
    # find first quoted string after the colon
    colon = rest.find(":")
    if colon == -1:
        return None
    quote_start = rest.find('"', colon)
    if quote_start == -1:
        return None
    quote_end = rest.find('"', quote_start + 1)
    if quote_end == -1:
        return None
    return rest[quote_start + 1:quote_end]


def create_issue(session: requests.Session, repo: str, issue: dict[str, Any]) -> dict[str, Any]:
    url = f"{GITHUB_API}/repos/{repo}/issues"
    payload = {
        "title": issue["title"],
        "body": issue["body"],
        "labels": list(issue.get("labels", []) or []),
    }
    response = _post(session, url, json=payload)
    return response.json()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def load_issues(config_path: Path) -> list[dict[str, Any]]:
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        raise ValueError(f"{config_path}: top-level `issues` must be a list")
    return issues


def main(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    cfg = load_config()
    session = github_session(cfg.github_token)

    issues = load_issues(config_path)
    logger.info("seed.start", extra={"repo": cfg.github_repo, "count": len(issues)})

    # 1. ensure all labels exist
    labels = _collect_labels(issues)
    logger.info("labels.ensure", extra={"count": len(labels)})
    ensure_labels(session, cfg.github_repo, labels)

    # 2. fetch existing issue bodies once for idempotency lookup
    existing_bodies = fetch_existing_issue_bodies(session, cfg.github_repo)

    created = 0
    skipped = 0
    for issue in issues:
        title = issue.get("title", "<no title>")
        body = issue.get("body", "") or ""
        key = _extract_idempotency_key(body)
        if not key:
            logger.warning("issue.no_idempotency_key", extra={"title": title})
            continue

        if _already_seeded(key, existing_bodies):
            logger.info("issue.skipped", extra={"title": title, "idempotency_key": key})
            skipped += 1
            continue

        result = create_issue(session, cfg.github_repo, issue)
        # add the newly-created body to the existing set so a duplicate key
        # within the same config file is still caught.
        existing_bodies.append(result.get("body") or body)
        logger.info(
            "issue.created",
            extra={
                "title": title,
                "idempotency_key": key,
                "number": result.get("number"),
            },
        )
        created += 1

    logger.info("seed.done", extra={"issues_created": created, "issues_skipped": skipped})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
