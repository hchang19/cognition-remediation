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
    python3 scripts/demo.py --verbose          # per-issue/per-session detail for live demos
    python3 scripts/demo.py --log              # re-enable structured JSON logs (for background runs)
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

try:
    from tqdm import tqdm as _tqdm, trange as _trange
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

import uuid

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


class _SandboxDevinClient:
    """Drop-in Devin client for sandbox runs — no API calls, fake session IDs.

    Lets the full orchestrator code path (routing, DB writes, event log) run
    without real API calls or cost. Simulates realistic status transitions:
      poll 0-1 → running, poll 2+ → completed
    """

    def __init__(self) -> None:
        self._poll_counts: dict[str, int] = {}

    def create_session(self, prompt: str, repo_url: str, issue_id: int) -> str:
        fake_id = f"sandbox-{uuid.uuid4().hex[:12]}"
        self._poll_counts[fake_id] = 0
        print(f"    [sandbox] fake session {fake_id}")
        return fake_id

    def get_session(self, session_id: str) -> "SessionResponse":  # type: ignore[name-defined]
        from app.devin_client import SessionResponse
        count = self._poll_counts.get(session_id, 0)
        self._poll_counts[session_id] = count + 1
        if count < 2:
            return SessionResponse(
                session_id=session_id, status="running", status_detail="working",
                cost_usd=None, session_url=f"https://app.devin.ai/sessions/{session_id}",
                pr_url=None, output=None,
            )
        return SessionResponse(
            session_id=session_id, status="completed", status_detail=None,
            cost_usd=0.12,
            session_url=f"https://app.devin.ai/sessions/{session_id}",
            pr_url=None, output="Sandbox: applied fix.",
        )

    def terminate_session(self, session_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def seed_issues(session, repo: str, verbose: bool = False) -> int:
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
            if verbose:
                labels = " ".join(f"[{l}]" for l in issue.get("labels", []))
                print(f"    skip  {issue.get('title', '')[:50]}  {labels}")
            continue
        result = create_issue(session, repo, issue)
        existing_bodies.append(result.get("body") or issue.get("body", ""))
        created += 1
        if verbose:
            labels = " ".join(f"[{l}]" for l in issue.get("labels", []))
            print(f"    new   #{result.get('number', '?')}  {issue.get('title', '')[:50]}  {labels}")

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
        SELECT s.session_id, s.pr_number, s.issue_id, i.complexity
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
        issue_id = row["issue_id"]
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
            continue

        # Close the linked issue now that the PR is merged.
        # GitHub auto-closes on "Closes #N" in the PR body, but we also close
        # explicitly here in case Devin omitted the keyword.
        try:
            gh.close_issue(issue_id)
            print(f"  issue #{issue_id} closed")
        except Exception as exc:
            print(f"  issue #{issue_id} close FAILED: {exc}")

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

    completed = failed = declined_count = merged_count = pending_count = 0
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
        elif row["status"] == "pending":
            pending_count += 1
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
        f"Pending: {pending_count}  |  "
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
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip issue seeding (use existing open issues only)",
    )
    parser.add_argument(
        "--seed-delay",
        type=int,
        default=0,
        metavar="SECONDS",
        help=(
            "Seconds to wait after seeding before dispatching Devin sessions. "
            "Useful in demos to observe the seeded issues before Devin picks them up "
            "(default: 0)."
        ),
    )
    parser.add_argument(
        "--dispatch-limit",
        type=int,
        default=None,
        metavar="N",
        help="Dispatch at most N issues (useful for sampling a subset in demos).",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help=(
            "Use a fake Devin client — exercises the full orchestrator routing and DB "
            "writes without real API calls or cost. Incompatible with --dry-run."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help=(
            "Demo-friendly verbose output: per-issue seed detail, per-session poll "
            "status with session URLs, and per-PR CI/commit breakdown."
        ),
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help=(
            "Re-enable INFO-level structured JSON logs from all app modules. "
            "Intended for background runs piped to a log file. "
            "Outputs to stderr alongside the normal demo output."
        ),
    )
    args = parser.parse_args()

    # --log: restore INFO-level structured logs suppressed by default above.
    if args.log:
        for _name in (
            "scripts.seed_issues", "app.orchestrator", "app.poller",
            "app.events", "app.db", "app.devin_client", "app.github_client",
        ):
            logging.getLogger(_name).setLevel(logging.INFO)

    print("\n=== Cognition Remediation Demo ===")

    cfg = load_config()
    db = get_db(cfg.db_path)
    gh_session = github_session(cfg.github_token)
    gh = GitHubClient(gh_session, cfg.github_repo)

    if args.sandbox:
        devin = _SandboxDevinClient()
        dry_run = False  # sandbox bypasses the dry_run gate intentionally
        print("  [sandbox] Using fake Devin client — no real API calls.")
    else:
        devin = DevinClient(cfg.devin_api_key, cfg.devin_org_id)
        dry_run = args.dry_run or cfg.pause or cfg.devin_daily_limit == 0

    # -----------------------------------------------------------------------
    # Step 1: Seed issues
    # -----------------------------------------------------------------------
    if args.no_seed:
        print("\nSkipping seed (--no-seed)")
    else:
        print("\nSeeding issues...", flush=True) if args.verbose else print("\nSeeding issues...", end=" ", flush=True)
        try:
            n_created = seed_issues(gh_session, cfg.github_repo, verbose=args.verbose)
            print(f"  done ({n_created} new)" if args.verbose else f"done ({n_created} new)")
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 1

        if args.seed_delay > 0 and not dry_run:
            if _HAS_TQDM:
                for _ in _trange(
                    args.seed_delay,
                    desc="  Seed delay",
                    unit="s",
                    ncols=72,
                    bar_format="{l_bar}{bar}| {n}/{total}s",
                ):
                    time.sleep(1)
            else:
                for remaining in range(args.seed_delay, 0, -1):
                    print(f"\r  {remaining}s remaining...   ", end="", flush=True)
                    time.sleep(1)
                print()

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
        dispatch_issues = issues[:args.dispatch_limit] if args.dispatch_limit else issues
        if args.dispatch_limit and len(issues) > args.dispatch_limit:
            print(f"Dispatching Devin sessions... (limit {args.dispatch_limit} of {len(issues)})")
        else:
            print("Dispatching Devin sessions...")
        for issue in dispatch_issues:
            complexity = next(
                (lb.split(":", 1)[1] for lb in issue.labels if lb.startswith("complexity:")),
                "ambiguous",
            )
            # Capture session count before to detect whether a new one was created
            before = db.execute("SELECT COUNT(*) FROM sessions WHERE issue_id=?", (issue.number,)).fetchone()[0]
            handle_issue(issue, db, devin, gh, cfg)
            after = db.execute("SELECT COUNT(*) FROM sessions WHERE issue_id=?", (issue.number,)).fetchone()[0]

            if after > before:
                sess = db.execute(
                    "SELECT session_id FROM sessions WHERE issue_id=? ORDER BY rowid DESC LIMIT 1",
                    (issue.number,),
                ).fetchone()
                sess_id = sess["session_id"] if sess else "?"
                print(f"  #{issue.number} {issue.title[:40]} ({complexity})  → session {sess_id}")
                if args.verbose:
                    label_str = "  ".join(issue.labels)
                    print(f"    labels: {label_str}")
                    print(f"    prompt: {'definite_prompt' if complexity == 'definite' else 'semi_definite_prompt'}")
            else:
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
        _poll_bar = (
            _tqdm(total=POLL_TIMEOUT, unit="s", ncols=72, desc="  Elapsed",
                  bar_format="{l_bar}{bar}| {n:.0f}/{total}s")
            if _HAS_TQDM else None
        )
        try:
            while True:
                active_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status IN ('running', 'pending')"
                ).fetchone()[0]
                if active_count == 0:
                    break

                elapsed = time.monotonic() - start
                if elapsed >= POLL_TIMEOUT:
                    print(f"  Timeout after {POLL_TIMEOUT // 60} minutes — moving on.")
                    break

                elapsed_intervals += 1
                minutes = int(elapsed_intervals * POLL_INTERVAL // 60)
                seconds = (elapsed_intervals * POLL_INTERVAL) % 60

                running_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='running'"
                ).fetchone()[0]
                pending_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='pending'"
                ).fetchone()[0]
                completed_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='completed'"
                ).fetchone()[0]
                failed_count = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status='failed'"
                ).fetchone()[0]

                status_line = (
                    f"  [{minutes:02d}:{seconds:02d}] "
                    f"{running_count} running, {pending_count} pending, "
                    f"{completed_count} completed, {failed_count} failed"
                )
                if _poll_bar:
                    _poll_bar.set_postfix_str(
                        f"{running_count} run  {pending_count} pend  "
                        f"{completed_count} done  {failed_count} fail"
                    )
                    _poll_bar.update(POLL_INTERVAL)
                else:
                    print(status_line)

                if args.verbose:
                    live = db.execute(
                        "SELECT s.session_id, s.status, s.status_detail, s.session_url, i.title "
                        "FROM sessions s JOIN issues i ON i.issue_id = s.issue_id "
                        "WHERE s.status IN ('running', 'pending')"
                    ).fetchall()
                    for s in live:
                        detail = f" [{s['status_detail']}]" if s["status_detail"] else ""
                        url_part = f"  {s['session_url']}" if s["session_url"] else ""
                        print(f"    {s['session_id'][:20]}  {s['status']}{detail}  {s['title'][:35]}{url_part}")

                time.sleep(POLL_INTERVAL)
                _poll_sessions(db, devin, gh=gh, cfg=cfg)

        except KeyboardInterrupt:
            print("\n  Interrupted — capturing current metrics.")
        finally:
            if _poll_bar:
                _poll_bar.close()

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
                if args.verbose:
                    ci_row = db.execute(
                        "SELECT ci_first_pass, human_intervened FROM sessions WHERE session_id=?",
                        (sess["session_id"],),
                    ).fetchone()
                    ci_str = _fmt_ci(ci_row["ci_first_pass"]) if ci_row else "-"
                    human_str = _fmt_bool(ci_row["human_intervened"]) if ci_row else "-"
                    print(
                        f"  #{sess['pr_number']}  commits={len(commits)}"
                        f"  CI={ci_str}  human_intervened={human_str}"
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
