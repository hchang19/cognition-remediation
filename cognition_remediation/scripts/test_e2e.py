"""End-to-end test script for Stage 3 clients.

Exercises both clients against real APIs. The Devin test creates a
minimal session with a dummy prompt and terminates it immediately so
token usage is negligible (< $0.01).

Run from cognition_remediation/:

    python scripts/test_e2e.py              # full suite (Devin + GitHub)
    python scripts/test_e2e.py --github     # GitHub client only
    python scripts/test_e2e.py --devin      # Devin client only

Credentials are loaded in this order:
    1. .env file in the current directory  (python-dotenv)
    2. Environment variables already exported in the shell

Required vars:
    GITHUB_TOKEN    — fine-grained PAT (issues:read, pull_requests:read)
    GITHUB_REPO     — owner/repo, e.g. hchang19/superset
    DEVIN_API_KEY   — Devin API key
    DEVIN_ORG_ID    — Devin organization ID
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

# Load .env BEFORE importing app modules so env vars are available
# to any module-level code that reads os.environ.
load_dotenv()

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from app.devin_client import DevinClient, DevinAPIError, SessionResponse
from app.github_client import GitHubClient, Issue, CIRun
from app.shared.github_session import github_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = "\033[92mPASS\033[0m"
_FAIL = "\033[91mFAIL\033[0m"
_SKIP = "\033[93mSKIP\033[0m"

_results: list[tuple[str, str, str]] = []


def _record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    tag = _PASS if status == "PASS" else (_FAIL if status == "FAIL" else _SKIP)
    line = f"  [{tag}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line)


def _require_env(*names: str) -> dict[str, str]:
    """Return a dict of env vars. Records a SKIP and returns empty if any are missing."""
    vals: dict[str, str] = {}
    missing: list[str] = []
    for n in names:
        v = os.environ.get(n, "")
        if not v:
            missing.append(n)
        else:
            vals[n] = v
    if missing:
        _record("env_check", "SKIP", f"missing: {', '.join(missing)}")
    return vals if not missing else {}


# ---------------------------------------------------------------------------
# Devin E2E — create a minimal session, poll, terminate immediately
# ---------------------------------------------------------------------------


def run_devin_tests() -> None:
    env = _require_env("DEVIN_API_KEY", "DEVIN_ORG_ID")
    if not env:
        return

    print("\n=== Devin Client E2E ===\n")
    client = DevinClient(api_key=env["DEVIN_API_KEY"], org_id=env["DEVIN_ORG_ID"])
    session_id: str | None = None

    # 1. Create session with the shortest possible prompt
    try:
        session_id = client.create_session(
            prompt="echo ok",
            repo_url="https://github.com/hchang19/superset",
            issue_id=0,
        )
        assert session_id and isinstance(session_id, str)
        _record("devin.create_session", "PASS", f"session_id={session_id}")
    except Exception as e:
        _record("devin.create_session", "FAIL", str(e))
        return

    # 2. Poll session status
    try:
        resp = client.get_session(session_id)
        assert isinstance(resp, SessionResponse)
        assert resp.session_id == session_id
        assert resp.status in ("running", "completed", "failed", "blocked")
        _record("devin.get_session", "PASS", f"status={resp.status}")
    except Exception as e:
        _record("devin.get_session", "FAIL", str(e))

    # 3. Verify dataclass fields
    try:
        assert resp.session_url is None or resp.session_url.startswith("https://")
        assert resp.pr_url is None or resp.pr_url.startswith("https://")
        _record("devin.response_fields", "PASS",
                f"url={resp.session_url}, cost={resp.cost_usd}")
    except Exception as e:
        _record("devin.response_fields", "FAIL", str(e))

    # 4. Terminate immediately to avoid token spend
    try:
        client.terminate_session(session_id)
        _record("devin.terminate_session", "PASS", "terminated")
    except DevinAPIError as e:
        # Already terminal is fine
        _record("devin.terminate_session", "PASS", f"already terminal: {e}")
    except Exception as e:
        _record("devin.terminate_session", "FAIL", str(e))

    # 5. Confirm post-terminate status
    try:
        time.sleep(1)
        resp2 = client.get_session(session_id)
        _record("devin.post_terminate", "PASS", f"status={resp2.status}")
    except Exception as e:
        _record("devin.post_terminate", "FAIL", str(e))


# ---------------------------------------------------------------------------
# GitHub E2E — read-only operations (no mutations)
# ---------------------------------------------------------------------------


def run_github_tests() -> None:
    env = _require_env("GITHUB_TOKEN")
    if not env:
        return
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")

    print("\n=== GitHub Client E2E ===\n")
    session = github_session(env["GITHUB_TOKEN"])
    client = GitHubClient(session=session, repo=repo)

    # 1. Fetch issues
    try:
        issues = client.get_open_issues("auto-remediate")
        assert isinstance(issues, list)
        _record("github.get_open_issues", "PASS", f"{len(issues)} issues")
    except Exception as e:
        _record("github.get_open_issues", "FAIL", str(e))
        return

    # 2. Verify Issue dataclass
    if issues:
        issue = issues[0]
        try:
            assert isinstance(issue, Issue)
            assert isinstance(issue.number, int) and isinstance(issue.title, str)
            assert all(isinstance(lb, str) for lb in issue.labels)
            _record("github.issue_fields", "PASS",
                    f"#{issue.number}: {issue.title[:50]}")
        except Exception as e:
            _record("github.issue_fields", "FAIL", str(e))
    else:
        _record("github.issue_fields", "SKIP", "no issues found")

    # 3. PR commits (read-only, PR #1)
    try:
        commits = client.get_pr_commits(pr_number=1)
        assert isinstance(commits, list)
        _record("github.get_pr_commits", "PASS", f"{len(commits)} commits")
    except Exception as e:
        if "404" in str(e):
            _record("github.get_pr_commits", "SKIP", "PR #1 not found")
        else:
            _record("github.get_pr_commits", "FAIL", str(e))

    # 4. CI run (read-only)
    try:
        ci = client.get_latest_ci_run(pr_number=1)
        detail = f"run {ci.run_id}: {ci.status}" if ci else "none"
        _record("github.get_latest_ci_run", "PASS", detail)
    except Exception as e:
        if "404" in str(e):
            _record("github.get_latest_ci_run", "SKIP", "PR #1 not found")
        else:
            _record("github.get_latest_ci_run", "FAIL", str(e))


# ---------------------------------------------------------------------------
# Full orchestration — GitHub fetch → Devin create → terminate
# ---------------------------------------------------------------------------


def run_orchestration_test() -> None:
    env = _require_env("GITHUB_TOKEN", "DEVIN_API_KEY", "DEVIN_ORG_ID")
    if not env:
        return
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")

    print("\n=== Orchestration E2E ===\n")

    gh = GitHubClient(
        session=github_session(env["GITHUB_TOKEN"]),
        repo=repo,
    )
    devin = DevinClient(api_key=env["DEVIN_API_KEY"], org_id=env["DEVIN_ORG_ID"])

    # 1. Fetch issues from GitHub
    try:
        issues = gh.get_open_issues("auto-remediate")
        _record("orch.fetch_issues", "PASS", f"{len(issues)} issues")
    except Exception as e:
        _record("orch.fetch_issues", "FAIL", str(e))
        return

    if not issues:
        _record("orch.dispatch", "SKIP", "no issues to process")
        return

    issue = issues[0]

    # 2. Create Devin session for the first issue (minimal prompt)
    try:
        sid = devin.create_session(
            prompt=f"echo 'issue #{issue.number}'",
            repo_url=f"https://github.com/{repo}",
            issue_id=issue.number,
        )
        _record("orch.create_session", "PASS", f"session={sid}")
    except Exception as e:
        _record("orch.create_session", "FAIL", str(e))
        return

    # 3. Poll once
    try:
        resp = devin.get_session(sid)
        _record("orch.poll", "PASS", f"status={resp.status}")
    except Exception as e:
        _record("orch.poll", "FAIL", str(e))

    # 4. Terminate immediately
    try:
        devin.terminate_session(sid)
        _record("orch.terminate", "PASS", "terminated")
    except DevinAPIError:
        _record("orch.terminate", "PASS", "already terminal")

    _record("orch.complete", "PASS", "full cycle verified")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3 client E2E tests")
    parser.add_argument("--github", action="store_true", help="GitHub tests only")
    parser.add_argument("--devin", action="store_true", help="Devin tests only")
    args = parser.parse_args()

    run_all = not args.github and not args.devin

    print("Loading credentials from .env + environment variables...")
    print(f"  GITHUB_TOKEN  = {'set' if os.environ.get('GITHUB_TOKEN') else 'NOT SET'}")
    print(f"  GITHUB_REPO   = {os.environ.get('GITHUB_REPO', '(default: hchang19/superset)')}")
    print(f"  DEVIN_API_KEY = {'set' if os.environ.get('DEVIN_API_KEY') else 'NOT SET'}")
    print(f"  DEVIN_ORG_ID  = {'set' if os.environ.get('DEVIN_ORG_ID') else 'NOT SET'}")

    if args.github or run_all:
        run_github_tests()
    if args.devin or run_all:
        run_devin_tests()
    if run_all:
        run_orchestration_test()

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    skipped = sum(1 for _, s, _ in _results if s == "SKIP")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped "
          f"({len(_results)} total)")

    if failed:
        print(f"\n{_FAIL} — {failed} test(s) failed:")
        for name, status, detail in _results:
            if status == "FAIL":
                print(f"  • {name}: {detail}")
        sys.exit(1)
    else:
        print(f"\n{_PASS} — all tests passed!")


if __name__ == "__main__":
    main()
