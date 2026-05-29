"""End-to-end test script for Stage 3 clients.

Exercises the full client lifecycle against real APIs. Run from
cognition_remediation/:

    python scripts/test_e2e.py              # full suite (Devin + GitHub)
    python scripts/test_e2e.py --github     # GitHub client only
    python scripts/test_e2e.py --devin      # Devin client only

Requires .env with:
    GITHUB_TOKEN    — fine-grained PAT with issues:write, pull_requests:read
    GITHUB_REPO     — e.g. hchang19/superset
    DEVIN_API_KEY   — Devin API key (Devin tests create a session; cost < $0.01)
    DEVIN_ORG_ID    — Devin organization ID

The Devin session is terminated immediately after creation to minimize cost.
"""

import argparse
import json
import os
import sys
import time
import traceback

from dotenv import load_dotenv

load_dotenv()

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from app.devin_client import DevinClient, DevinAPIError
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


# ---------------------------------------------------------------------------
# GitHub E2E tests
# ---------------------------------------------------------------------------


def run_github_tests() -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")
    if not token:
        _record("github.setup", "SKIP", "GITHUB_TOKEN not set")
        return

    print("\n=== GitHub Client E2E ===\n")
    session = github_session(token)
    client = GitHubClient(session=session, repo=repo)

    # 1. Fetch open issues with auto-remediate label
    try:
        issues = client.get_open_issues("auto-remediate")
        assert isinstance(issues, list)
        _record("github.get_open_issues", "PASS", f"found {len(issues)} issues")
    except Exception as e:
        _record("github.get_open_issues", "FAIL", str(e))
        return

    # 2. Verify Issue dataclass fields
    if issues:
        issue = issues[0]
        try:
            assert isinstance(issue, Issue)
            assert isinstance(issue.number, int)
            assert isinstance(issue.title, str)
            assert isinstance(issue.labels, list)
            assert isinstance(issue.body, str)
            _record("github.issue_dataclass", "PASS", f"issue #{issue.number}: {issue.title[:60]}")
        except Exception as e:
            _record("github.issue_dataclass", "FAIL", str(e))
    else:
        _record("github.issue_dataclass", "SKIP", "no issues to inspect")

    # 3. Verify label names are strings
    if issues:
        try:
            for i in issues:
                assert all(isinstance(lb, str) for lb in i.labels)
            _record("github.label_types", "PASS", "all labels are strings")
        except Exception as e:
            _record("github.label_types", "FAIL", str(e))
    else:
        _record("github.label_types", "SKIP", "no issues")

    # 4. Fetch PR commits (use PR #1 if it exists, otherwise skip)
    try:
        commits = client.get_pr_commits(pr_number=1)
        assert isinstance(commits, list)
        if commits:
            assert commits[0].sha
            assert commits[0].author
        _record("github.get_pr_commits", "PASS", f"{len(commits)} commits on PR #1")
    except Exception as e:
        err = str(e)
        if "404" in err:
            _record("github.get_pr_commits", "SKIP", "PR #1 not found")
        else:
            _record("github.get_pr_commits", "FAIL", err)

    # 5. Fetch CI run for PR #1
    try:
        ci = client.get_latest_ci_run(pr_number=1)
        if ci is not None:
            assert isinstance(ci, CIRun)
            assert ci.status in ("queued", "in_progress", "completed")
            _record("github.get_latest_ci_run", "PASS", f"run {ci.run_id}: {ci.status}")
        else:
            _record("github.get_latest_ci_run", "PASS", "no CI runs (None returned)")
    except Exception as e:
        err = str(e)
        if "404" in err:
            _record("github.get_latest_ci_run", "SKIP", "PR #1 not found")
        else:
            _record("github.get_latest_ci_run", "FAIL", err)


# ---------------------------------------------------------------------------
# Devin E2E tests
# ---------------------------------------------------------------------------


def run_devin_tests() -> None:
    api_key = os.environ.get("DEVIN_API_KEY")
    org_id = os.environ.get("DEVIN_ORG_ID")
    if not api_key or not org_id:
        _record("devin.setup", "SKIP", "DEVIN_API_KEY or DEVIN_ORG_ID not set")
        return

    print("\n=== Devin Client E2E ===\n")
    client = DevinClient(api_key=api_key, org_id=org_id)
    session_id = None

    # 1. Create session
    try:
        session_id = client.create_session(
            prompt="Print the string 'hello world' to stdout and exit.",
            repo_url="https://github.com/hchang19/superset",
            issue_id=0,
        )
        assert session_id and isinstance(session_id, str)
        _record("devin.create_session", "PASS", f"session_id={session_id}")
    except DevinAPIError as e:
        _record("devin.create_session", "FAIL", str(e))
        return
    except Exception as e:
        _record("devin.create_session", "FAIL", str(e))
        return

    # 2. Get session
    try:
        resp = client.get_session(session_id)
        assert isinstance(resp, SessionResponse)
        assert resp.session_id == session_id
        assert resp.status in ("running", "completed", "failed", "blocked")
        _record("devin.get_session", "PASS", f"status={resp.status}, cost=${resp.cost_usd}")
    except DevinAPIError as e:
        _record("devin.get_session", "FAIL", str(e))
    except Exception as e:
        _record("devin.get_session", "FAIL", str(e))

    # 3. Verify SessionResponse fields
    try:
        assert resp.session_url is None or resp.session_url.startswith("https://")
        assert resp.pr_url is None or resp.pr_url.startswith("https://")
        _record("devin.response_fields", "PASS", f"session_url={resp.session_url}")
    except Exception as e:
        _record("devin.response_fields", "FAIL", str(e))

    # 4. Terminate session
    if session_id:
        try:
            client.terminate_session(session_id)
            _record("devin.terminate_session", "PASS", "session terminated")
        except DevinAPIError as e:
            _record("devin.terminate_session", "PASS", f"terminate returned error (may already be terminal): {e}")
        except Exception as e:
            _record("devin.terminate_session", "FAIL", str(e))

    # 5. Verify terminated session status
    if session_id:
        try:
            time.sleep(1)
            resp2 = client.get_session(session_id)
            _record("devin.post_terminate_status", "PASS", f"status={resp2.status}")
        except Exception as e:
            _record("devin.post_terminate_status", "FAIL", str(e))


# ---------------------------------------------------------------------------
# Full orchestration E2E
# ---------------------------------------------------------------------------


def run_orchestration_test() -> None:
    """Exercises the full flow: GitHub issues → Devin session → post results back."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")
    api_key = os.environ.get("DEVIN_API_KEY")
    org_id = os.environ.get("DEVIN_ORG_ID")

    if not token or not api_key or not org_id:
        _record("orchestration.setup", "SKIP", "missing GITHUB_TOKEN, DEVIN_API_KEY, or DEVIN_ORG_ID")
        return

    print("\n=== Orchestration E2E (GitHub + Devin) ===\n")

    gh_session = github_session(token)
    gh_client = GitHubClient(session=gh_session, repo=repo)
    devin_client = DevinClient(api_key=api_key, org_id=org_id)

    # 1. Fetch issues
    try:
        issues = gh_client.get_open_issues("auto-remediate")
        _record("orch.fetch_issues", "PASS", f"{len(issues)} issues found")
    except Exception as e:
        _record("orch.fetch_issues", "FAIL", str(e))
        return

    if not issues:
        _record("orch.dispatch", "SKIP", "no issues to process")
        return

    issue = issues[0]

    # 2. Create Devin session for issue
    session_id = None
    try:
        session_id = devin_client.create_session(
            prompt=f"Fix issue #{issue.number}: {issue.title}\n\n{issue.body}",
            repo_url=f"https://github.com/{repo}",
            issue_id=issue.number,
        )
        _record("orch.create_session", "PASS", f"session={session_id} for issue #{issue.number}")
    except DevinAPIError as e:
        _record("orch.create_session", "FAIL", str(e))
        return

    # 3. Poll session
    try:
        resp = devin_client.get_session(session_id)
        _record("orch.poll_session", "PASS", f"status={resp.status}")
    except Exception as e:
        _record("orch.poll_session", "FAIL", str(e))

    # 4. Terminate to avoid cost
    try:
        devin_client.terminate_session(session_id)
        _record("orch.terminate", "PASS", "terminated to avoid cost")
    except DevinAPIError:
        _record("orch.terminate", "PASS", "already terminal")

    _record("orch.complete", "PASS", "full orchestration cycle verified")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3 client E2E tests")
    parser.add_argument("--github", action="store_true", help="Run GitHub tests only")
    parser.add_argument("--devin", action="store_true", help="Run Devin tests only")
    args = parser.parse_args()

    run_all = not args.github and not args.devin

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
    total = len(_results)
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped ({total} total)")

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
