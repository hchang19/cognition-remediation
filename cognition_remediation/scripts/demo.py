"""Full pipeline demo: seed → dispatch → poll → metrics → (optional) auto-merge.

Runs the complete remediation pipeline end-to-end:
  1. Load .env, open DB, create clients
  2. Seed issues from config/issues.yml
  3. Fetch open auto-remediate issues via GitHubClient
  4. Dispatch each issue to handle_issue() (creates Devin sessions)
  5. Poll loop: every 30s until no running sessions (or 30min timeout)
  6. Capture PR commits + CI outcomes via _poll_prs()
  7. (Optional) Auto-merge eligible PRs
  8. Print a metrics table

Run:
    python3 scripts/demo.py
    python3 scripts/demo.py --dry-run          # skip polling, show existing DB data
    python3 scripts/demo.py --auto-merge       # merge complexity:definite PRs that pass CI
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "..", "pypackages"))

from dotenv import load_dotenv

load_dotenv()

from app.db import get_db
from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.orchestrator import handle_issue
from app.poller import _poll_prs, _poll_sessions
from app.shared.config import load_config
from app.shared.github_session import github_session
from app.shared.logger import get_logger
from scripts.seed_issues import (
    DEFAULT_CONFIG_PATH,
    _already_seeded,
    _collect_labels,
    _extract_idempotency_key,
    create_issue,
    ensure_labels,
    fetch_existing_issue_bodies,
    load_issues,
)

# Suppress INFO logs from the seeder so the output stays clean.
logging.getLogger("scripts.seed_issues").setLevel(logging.WARNING)
# Also suppress internal orchestrator/poller noise from demo output.
logging.getLogger("app.orchestrator").setLevel(logging.WARNING)
logging.getLogger("app.poller").setLevel(logging.WARNING)
logging.getLogger("app.events").setLevel(logging.WARNING)
logging.getLogger("app.db").setLevel(logging.WARNING)
logging.getLogger("app.devin_client").setLevel(logging.WARNING)
logging.getLogger("app.github_client").setLevel(logging.WARNING)

logger = get_logger(__name__)

POLL_INTERVAL = 30          # seconds between each _poll_sessions call
POLL_TIMEOUT = 30 * 60      # 30-minute hard stop


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def seed_issues(session, repo: str) -> int:
    """Seed issues.yml into the repo. Returns count of issues created."""
    issues_cfg = load_issues(DEFAULT_CONFIG_PATH)
    ensure_labels(session, repo, _collect_labels(issues_cfg))
    existing_bodies = fetch_existing_issue_bodies(session, repo)

    created = 0
    for issue in issues_cfg:
        key = _extract_idempotency_key(issue.get("body", "") or "")
        if not key:
            continue
        if _already_seeded(key, existing_bodies):
            continue
        result = create_issue(session, repo, issue)
        existing_bodies.append(result.get("body") or issue.get("body", ""))
        created += 1

    return created


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def _fmt_cost(cost) -> str:
    if cost is None:
        return "-"
    return f"${cost:.2f}"


def _fmt_ci(ci_first_pass) -> str:
    if ci_first_pass is None:
        return "-"
    return "PASS" if ci_first_pass else "FAIL"


def _fmt_bool(val) -> str:
    if val is None:
        return "-"
    return "Yes" if val else "No"


def _fmt_int(val) -> str:
    return str(val) if val is not None else "-"


def _fmt_pr(val) -> str:
    return f"#{val}" if val is not None else "-"


def _cycle_minutes(session_row, db) -> str:
    """Return cycle time in minutes: issue created_at to session completed_at."""
    if not session_row["completed_at"]:
        return "-"
    issue_row = db.execute(
        "SELECT created_at FROM issues WHERE issue_id = ?",
        (session_row["issue_id"],),
    ).fetchone()
    if not issue_row:
        return "-"
    try:
        from datetime import datetime, timezone

        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        def _parse(ts):
            # Try with microseconds first, then without
            for f in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    return datetime.strptime(ts, f)
                except ValueError:
                    continue
            return None

        t0 = _parse(issue_row["created_at"])
        t1 = _parse(session_row["completed_at"])
        if t0 and t1:
            return f"{int((t1 - t0).total_seconds() / 60)}m"
    except Exception:
        pass
    return "-"


def merge_eligible_prs(db, gh: GitHubClient) -> list[int]:
    """Merge PRs from completed, definite-complexity, CI-passing, no-human-intervention sessions.

    A PR is eligible when:
      - session status = "completed"
      - complexity = "definite"  (well-scoped, no ambiguity)
      - ci_first_pass = 1        (all tests pass on first run)
      - human_intervened = 0/NULL (Devin authored every commit)
      - pr_merged IS NULL        (not already merged by this script)

    Returns the list of merged PR numbers.
    """
    rows = db.execute(
        """
        SELECT s.session_id, s.pr_number, i.complexity
        FROM sessions s
        JOIN issues i ON i.issue_id = s.issue_id
        WHERE s.status = 'completed'
          AND s.pr_number IS NOT NULL
          AND s.pr_merged IS NULL
          AND s.ci_first_pass = 1
          AND (s.human_intervened = 0 OR s.human_intervened IS NULL)
          AND i.complexity = 'definite'
        """
    ).fetchall()

    merged: list[int] = []
    for row in rows:
        pr = row["pr_number"]
        try:
            gh.merge_pr(pr)
            with db:
                db.execute(
                    "UPDATE sessions SET pr_merged=1 WHERE session_id=?",
                    (row["session_id"],),
                )
            merged.append(pr)
            print(f"  #{pr} merged  (complexity:definite, CI passed, no human commits)")
        except Exception as exc:
            print(f"  #{pr} merge FAILED: {exc}")

    return merged


def print_metrics_table(db) -> None:
    rows = db.execute(
        """
        SELECT s.session_id, s.issue_id, s.status, s.cost_usd,
               s.ci_first_pass, s.human_intervened, s.commits_count,
               s.session_url, s.pr_number, s.completed_at, s.pr_merged,
               i.title
        FROM sessions s
        JOIN issues i ON i.issue_id = s.issue_id
        ORDER BY s.issue_id
        """
    ).fetchall()

    # Also pull declined issues (they have no sessions row)
    declined_issue_ids = set()
    declined_rows = db.execute(
        """
        SELECT DISTINCT e.issue_id, i.title
        FROM events e
        JOIN issues i ON i.issue_id = e.issue_id
        WHERE e.event_type = 'session.declined'
        """
    ).fetchall()

    print("\n=== Session Metrics ===")
    hdr = f"{'#':<5} {'Title':<26} {'Status':<11} {'Cost':<8} {'CI':<6} {'Human':<7} {'Commits':<9} {'PR':<5} {'Cycle'}"
    print(hdr)
    print("-" * len(hdr))

    completed = failed = declined_count = merged_count = 0
    total_cost = 0.0
    ci_passes = ci_total = human_count = 0

    for row in rows:
        title = row["title"][:25] if row["title"] else ""
        cycle = _cycle_minutes(row, db)
        display_status = "merged" if row["pr_merged"] else row["status"]
        print(
            f"#{row['issue_id']:<4} {title:<26} {display_status:<11} "
            f"{_fmt_cost(row['cost_usd']):<8} {_fmt_ci(row['ci_first_pass']):<6} "
            f"{_fmt_bool(row['human_intervened']):<7} {_fmt_int(row['commits_count']):<9} "
            f"{_fmt_pr(row['pr_number']):<5} {cycle}"
        )
        if row["status"] == "completed":
            completed += 1
            if row["pr_merged"]:
                merged_count += 1
        elif row["status"] == "failed":
            failed += 1
        if row["cost_usd"] is not None:
            total_cost += row["cost_usd"]
        if row["ci_first_pass"] is not None:
            ci_total += 1
            if row["ci_first_pass"]:
                ci_passes += 1
        if row["human_intervened"]:
            human_count += 1

    for dec_row in declined_rows:
        title = dec_row["title"][:25] if dec_row["title"] else ""
        print(f"#{dec_row['issue_id']:<4} {title:<26} {'declined':<11} {'-':<8} {'-':<6} {'-':<7} {'-':<9} {'-':<5} -")
        declined_count += 1

    total_sessions = len(rows) + declined_count
    print()
    print("=== Summary ===")
    print(
        f"Total sessions: {total_sessions}  |  "
        f"Completed: {completed}  |  "
        f"Merged: {merged_count}  |  "
        f"Failed: {failed}  |  "
        f"Declined: {declined_count}"
    )
    ci_rate = f"{100 * ci_passes // ci_total}%" if ci_total else "N/A"
    human_rate = f"{100 * human_count // completed}%" if completed else "N/A"
    avg_cost = f"${total_cost / (completed + failed):.2f}" if (completed + failed) else "N/A"
    print(
        f"Avg cost: {avg_cost}    |  "
        f"CI pass rate: {ci_rate}  |  "
        f"Human intervention: {human_rate}"
    )
    print(f"Total spend: ${total_cost:.2f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Cognition Remediation Demo")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip polling; print existing DB metrics only",
    )
    parser.add_argument(
        "--auto-merge",
        action="store_true",
        help=(
            "After sessions complete, squash-merge PRs where complexity=definite, "
            "CI passes on first run, and no human commits were detected. "
            "Without this flag, PRs are opened but never merged."
        ),
    )
    args = parser.parse_args()

    print("\n=== Cognition Remediation Demo ===")

    cfg = load_config()
    db = get_db(cfg.db_path)
    gh_session = github_session(cfg.github_token)
    gh = GitHubClient(gh_session, cfg.github_repo)
    devin = DevinClient(cfg.devin_api_key, cfg.devin_org_id)

    dry_run = args.dry_run or cfg.pause or cfg.devin_daily_limit == 0

    # -----------------------------------------------------------------------
    # Step 1: Seed issues
    # -----------------------------------------------------------------------
    print("\nSeeding issues...", end=" ", flush=True)
    try:
        n_created = seed_issues(gh_session, cfg.github_repo)
        print(f"done ({n_created} new)")
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    # -----------------------------------------------------------------------
    # Step 2: Fetch open auto-remediate issues
    # -----------------------------------------------------------------------
    print("Fetching open issues...", end=" ", flush=True)
    try:
        issues = gh.get_open_issues("auto-remediate")
        print(f"done ({len(issues)} auto-remediate)")
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    if not issues:
        print("No auto-remediate issues found — nothing to dispatch.")
        print_metrics_table(db)
        db.close()
        return 0

    # -----------------------------------------------------------------------
    # Step 3: Dispatch — call handle_issue for each
    # -----------------------------------------------------------------------
    if not dry_run:
        print("Dispatching Devin sessions...")
        for issue in issues:
            complexity = next(
                (lb.split(":", 1)[1] for lb in issue.labels if lb.startswith("complexity:")),
                "ambiguous",
            )
            # Capture session count before to detect whether a new one was created
            before = db.execute("SELECT COUNT(*) FROM sessions WHERE issue_id=?", (issue.number,)).fetchone()[0]
            handle_issue(issue, db, devin, gh, cfg)
            after = db.execute("SELECT COUNT(*) FROM sessions WHERE issue_id=?", (issue.number,)).fetchone()[0]

            if after > before:
                # New session was created — find its session_id
                sess = db.execute(
                    "SELECT session_id FROM sessions WHERE issue_id=? ORDER BY rowid DESC LIMIT 1",
                    (issue.number,),
                ).fetchone()
                sess_id = sess["session_id"] if sess else "?"
                print(f"  #{issue.number} {issue.title[:40]} ({complexity})  → session {sess_id}")
            else:
                # Check if declined
                declined = db.execute(
                    "SELECT 1 FROM events WHERE issue_id=? AND event_type='session.declined'",
                    (issue.number,),
                ).fetchone()
                if declined:
                    print(f"  #{issue.number} {issue.title[:40]} ({complexity})  → declined (needs-human-scoping)")
                else:
                    print(f"  #{issue.number} {issue.title[:40]} ({complexity})  → skipped (limit/active session)")

    # -----------------------------------------------------------------------
    # Step 4: Poll loop
    # -----------------------------------------------------------------------
    if not dry_run:
        print("\nPolling for completion... (Ctrl+C to stop)")
        start = time.monotonic()
        elapsed_intervals = 0
        try:
            while True:
                running_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='running'"
                ).fetchone()[0]
                if running_count == 0:
                    break

                elapsed = time.monotonic() - start
                if elapsed >= POLL_TIMEOUT:
                    print(f"  Timeout after {POLL_TIMEOUT // 60} minutes — moving on.")
                    break

                elapsed_intervals += 1
                minutes = int(elapsed_intervals * POLL_INTERVAL // 60)
                seconds = (elapsed_intervals * POLL_INTERVAL) % 60

                completed_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='completed'"
                ).fetchone()[0]
                failed_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='failed'"
                ).fetchone()[0]
                print(
                    f"  [{minutes:02d}:{seconds:02d}] "
                    f"{running_count} running, {completed_count} completed, {failed_count} failed"
                )

                time.sleep(POLL_INTERVAL)
                _poll_sessions(db, devin)

        except KeyboardInterrupt:
            print("\n  Interrupted — capturing current metrics.")

    # -----------------------------------------------------------------------
    # Step 5: Capture PR metrics
    # -----------------------------------------------------------------------
    print("\nCapturing PR metrics...")
    try:
        _poll_prs(db, gh)

        # Update commits_count for completed sessions with a pr_number
        completed_sessions = db.execute(
            "SELECT session_id, pr_number FROM sessions WHERE status='completed' AND pr_number IS NOT NULL"
        ).fetchall()
        for sess in completed_sessions:
            try:
                commits = gh.get_pr_commits(sess["pr_number"])
                with db:
                    db.execute(
                        "UPDATE sessions SET commits_count=? WHERE session_id=?",
                        (len(commits), sess["session_id"]),
                    )
            except Exception as exc:
                logger.warning("demo.commits_count_failed", extra={"pr_number": sess["pr_number"], "error": str(exc)})
    except Exception as exc:
        print(f"  WARNING: PR metrics capture failed: {exc}")

    # -----------------------------------------------------------------------
    # Step 6: Auto-merge eligible PRs (opt-in)
    # -----------------------------------------------------------------------
    if args.auto_merge:
        print("\nAuto-merging eligible PRs...")
        merged = merge_eligible_prs(db, gh)
        if not merged:
            print("  No PRs eligible for auto-merge.")

    # -----------------------------------------------------------------------
    # Step 7: Print metrics table
    # -----------------------------------------------------------------------
    print_metrics_table(db)

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
