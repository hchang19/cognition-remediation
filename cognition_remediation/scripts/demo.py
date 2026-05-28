"""Demo runner: seed issues then verify they exist on GitHub.

Steps:
  1. Seed issues.yml into the configured GitHub repo (idempotent)
  2. Fetch all open issues from the repo and print a summary table

Run:
    cd cognition_remediation
    python3 -m scripts.demo
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from app.shared.config import load_config
from app.shared.github_session import github_session
from app.shared.logger import get_logger
from scripts.seed_issues import (
    DEFAULT_CONFIG_PATH,
    GITHUB_API,
    ensure_labels,
    fetch_existing_issue_bodies,
    load_issues,
    _collect_labels,
    _extract_idempotency_key,
    _already_seeded,
    create_issue,
)

logger = get_logger(__name__)


def fetch_open_issues(session, repo: str) -> list[dict]:
    """Return all open issues (excluding PRs) from the repo."""
    issues = []
    params = {"state": "open", "per_page": 100, "page": 1}
    while True:
        r = session.get(f"{GITHUB_API}/repos/{repo}/issues", params=params)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for item in batch:
            if "pull_request" not in item:
                issues.append(item)
        if len(batch) < params["per_page"]:
            break
        params["page"] += 1
    return issues


def print_issues_table(issues: list[dict]) -> None:
    print(f"\n{'#':<6} {'Title':<60} {'Labels'}")
    print("-" * 100)
    for issue in sorted(issues, key=lambda i: i["number"]):
        labels = ", ".join(l["name"] for l in issue.get("labels", []))
        title = issue["title"][:57] + "..." if len(issue["title"]) > 60 else issue["title"]
        print(f"#{issue['number']:<5} {title:<60} {labels or '(none)'}")
    print()


def main() -> int:
    cfg = load_config()
    session = github_session(cfg.github_token)

    # Step 1: Seed
    print("\n=== Step 1: Seeding issues ===")
    issues_cfg = load_issues(DEFAULT_CONFIG_PATH)
    ensure_labels(session, cfg.github_repo, _collect_labels(issues_cfg))
    existing_bodies = fetch_existing_issue_bodies(session, cfg.github_repo)

    created = skipped = 0
    for issue in issues_cfg:
        key = _extract_idempotency_key(issue.get("body", "") or "")
        if not key:
            print(f"  WARN  no idempotency_key: {issue.get('title')}")
            continue
        if _already_seeded(key, existing_bodies):
            print(f"  SKIP  {issue['title']}")
            skipped += 1
        else:
            result = create_issue(session, cfg.github_repo, issue)
            existing_bodies.append(result.get("body") or issue.get("body", ""))
            print(f"  CREATE #{result.get('number')}  {issue['title']}")
            created += 1

    print(f"\n  Done — created: {created}, skipped: {skipped}")

    # Step 2: Verify
    print("\n=== Step 2: Fetching open issues from GitHub ===")
    open_issues = fetch_open_issues(session, cfg.github_repo)
    print(f"  Found {len(open_issues)} open issue(s) in {cfg.github_repo}")
    print_issues_table(open_issues)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
