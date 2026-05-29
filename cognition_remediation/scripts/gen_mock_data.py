"""Populate the SQLite database with realistic mock data for dashboard development.

Inserts a set of issues, sessions, and events that exercise every dashboard panel:
  - KPI strip (total issues, sessions, PRs, success rate, cost, blocked)
  - Session timeline (events across multiple days)
  - Complexity breakdown (definite / semi-definite / ambiguous success rates)
  - Per-issue detail table (all column variations)

Safe to re-run: clears all tables before inserting so the result is always clean.

Run:
    python3 -m scripts.gen_mock_data
or
    python3 cognition_remediation/scripts/gen_mock_data.py [--db PATH]

DB_PATH env var (or --db flag) controls output location; defaults to cognition.db.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.db import get_db  # noqa: E402
from app.shared.config import load_config  # noqa: E402
from app.shared.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2026, 5, 25, 9, 0, 0, tzinfo=timezone.utc)


def _ts(days: float = 0, hours: float = 0, minutes: float = 0) -> str:
    return (_BASE + timedelta(days=days, hours=hours, minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# Mock issues
# Mirrors the complexity/type/severity mix in config/issues.yml
# ---------------------------------------------------------------------------

ISSUES = [
    # definite — vulnerability
    {
        "issue_id": 1,
        "title": "Upgrade urllib3 from 1.26.5 to 1.26.18 (CVE-2023-45803)",
        "complexity": "definite",
        "source": "pip-audit",
        "severity": "high",
        "state": "closed",
        "created_at": _ts(0, 0),
        "closed_at": _ts(0, 3),
        "reopened_at": None,
    },
    # definite — upgrade
    {
        "issue_id": 2,
        "title": "Upgrade Pillow to >=10.0.1 (outdated, upstream EOL)",
        "complexity": "definite",
        "source": "manual",
        "severity": "medium",
        "state": "closed",
        "created_at": _ts(0, 1),
        "closed_at": _ts(0, 4),
        "reopened_at": None,
    },
    # definite — bug
    {
        "issue_id": 3,
        "title": "parse_human_datetime() raises ValueError on empty string input",
        "complexity": "definite",
        "source": "manual",
        "severity": "medium",
        "state": "closed",
        "created_at": _ts(0, 2),
        "closed_at": _ts(1, 0),
        "reopened_at": None,
    },
    # definite — failed session
    {
        "issue_id": 4,
        "title": "Rotate all hardcoded API keys and secrets to environment variables",
        "complexity": "definite",
        "source": "manual",
        "severity": "critical",
        "state": "open",
        "created_at": _ts(1, 0),
        "closed_at": None,
        "reopened_at": None,
    },
    # definite — blocked (cost cap)
    {
        "issue_id": 5,
        "title": "Add database index on slices.datasource_id to fix slow dashboard load",
        "complexity": "definite",
        "source": "manual",
        "severity": "medium",
        "state": "open",
        "created_at": _ts(1, 2),
        "closed_at": None,
        "reopened_at": None,
    },
    # semi-definite — completed with human intervention
    {
        "issue_id": 6,
        "title": "Dashboard filters silently fail for Unicode characters in filter values",
        "complexity": "semi-definite",
        "source": "manual",
        "severity": "high",
        "state": "closed",
        "created_at": _ts(1, 4),
        "closed_at": _ts(2, 6),
        "reopened_at": None,
    },
    # semi-definite — completed, reopened once
    {
        "issue_id": 7,
        "title": "Add CSV export option to SQL Lab results panel",
        "complexity": "semi-definite",
        "source": "manual",
        "severity": "low",
        "state": "closed",
        "created_at": _ts(2, 0),
        "closed_at": _ts(3, 2),
        "reopened_at": _ts(2, 8),
    },
    # semi-definite — pending (waiting for human)
    {
        "issue_id": 8,
        "title": "Investigate and fix intermittent OOM crash in production chart renderer",
        "complexity": "semi-definite",
        "source": "manual",
        "severity": "high",
        "state": "open",
        "created_at": _ts(2, 6),
        "closed_at": None,
        "reopened_at": None,
    },
    # ambiguous — declined (no session)
    {
        "issue_id": 9,
        "title": "Make the dashboard rendering faster",
        "complexity": "ambiguous",
        "source": "manual",
        "severity": "low",
        "state": "open",
        "created_at": _ts(3, 0),
        "closed_at": None,
        "reopened_at": None,
    },
    # definite — running right now
    {
        "issue_id": 10,
        "title": "Fix missing CSRF token on chart embed endpoint",
        "complexity": "definite",
        "source": "pip-audit",
        "severity": "high",
        "state": "open",
        "created_at": _ts(3, 2),
        "closed_at": None,
        "reopened_at": None,
    },
]

# ---------------------------------------------------------------------------
# Mock sessions
# ---------------------------------------------------------------------------

SESSIONS = [
    # issue 1 — completed cleanly, CI pass, one PR merged
    {
        "session_id": "sess-aa1111",
        "issue_id": 1,
        "status": "completed",
        "created_at": _ts(0, 0, 5),
        "completed_at": _ts(0, 2, 50),
        "cost_usd": 0.87,
        "session_url": "https://app.devin.ai/sessions/sess-aa1111",
        "pr_number": 10,
        "commits_count": 2,
        "ci_first_pass": 1,
        "human_intervened": 0,
        "duration_seconds": int(timedelta(hours=2, minutes=45).total_seconds()),
        "pr_merged": 1,
        "status_detail": None,
    },
    # issue 2 — completed, minor human review
    {
        "session_id": "sess-bb2222",
        "issue_id": 2,
        "status": "completed",
        "created_at": _ts(0, 1, 5),
        "completed_at": _ts(0, 3, 55),
        "cost_usd": 1.12,
        "session_url": "https://app.devin.ai/sessions/sess-bb2222",
        "pr_number": 11,
        "commits_count": 3,
        "ci_first_pass": 1,
        "human_intervened": 0,
        "duration_seconds": int(timedelta(hours=2, minutes=50).total_seconds()),
        "pr_merged": 1,
        "status_detail": None,
    },
    # issue 3 — completed, CI failed first time (ci_first_pass=0), human merged
    {
        "session_id": "sess-cc3333",
        "issue_id": 3,
        "status": "completed",
        "created_at": _ts(0, 2, 10),
        "completed_at": _ts(0, 23, 0),
        "cost_usd": 2.34,
        "session_url": "https://app.devin.ai/sessions/sess-cc3333",
        "pr_number": 12,
        "commits_count": 5,
        "ci_first_pass": 0,
        "human_intervened": 1,
        "duration_seconds": int(timedelta(hours=20, minutes=50).total_seconds()),
        "pr_merged": 1,
        "status_detail": None,
    },
    # issue 4 — failed (too many files, hit cost cap mid-way)
    {
        "session_id": "sess-dd4444",
        "issue_id": 4,
        "status": "failed",
        "created_at": _ts(1, 0, 15),
        "completed_at": _ts(1, 4, 0),
        "cost_usd": 5.00,
        "session_url": "https://app.devin.ai/sessions/sess-dd4444",
        "pr_number": None,
        "commits_count": 0,
        "ci_first_pass": None,
        "human_intervened": 0,
        "duration_seconds": int(timedelta(hours=3, minutes=45).total_seconds()),
        "pr_merged": 0,
        "status_detail": None,
    },
    # issue 5 — blocked (out of credits)
    {
        "session_id": "sess-ee5555",
        "issue_id": 5,
        "status": "blocked",
        "created_at": _ts(1, 2, 20),
        "completed_at": _ts(1, 7, 0),
        "cost_usd": 5.00,
        "session_url": "https://app.devin.ai/sessions/sess-ee5555",
        "pr_number": None,
        "commits_count": 0,
        "ci_first_pass": None,
        "human_intervened": 0,
        "duration_seconds": int(timedelta(hours=4, minutes=40).total_seconds()),
        "pr_merged": 0,
        "status_detail": "usage_limit_exceeded",
    },
    # issue 6 — completed with human intervention (semi-definite, harder)
    {
        "session_id": "sess-ff6666",
        "issue_id": 6,
        "status": "completed",
        "created_at": _ts(1, 4, 30),
        "completed_at": _ts(2, 5, 45),
        "cost_usd": 3.67,
        "session_url": "https://app.devin.ai/sessions/sess-ff6666",
        "pr_number": 13,
        "commits_count": 7,
        "ci_first_pass": 1,
        "human_intervened": 1,
        "duration_seconds": int(timedelta(hours=25, minutes=15).total_seconds()),
        "pr_merged": 1,
        "status_detail": None,
    },
    # issue 7 — completed, reopened (penalty in efficiency score)
    {
        "session_id": "sess-gg7777",
        "issue_id": 7,
        "status": "completed",
        "created_at": _ts(2, 0, 10),
        "completed_at": _ts(3, 1, 55),
        "cost_usd": 2.95,
        "session_url": "https://app.devin.ai/sessions/sess-gg7777",
        "pr_number": 14,
        "commits_count": 4,
        "ci_first_pass": 1,
        "human_intervened": 0,
        "duration_seconds": int(timedelta(hours=25, minutes=45).total_seconds()),
        "pr_merged": 1,
        "status_detail": None,
    },
    # issue 8 — pending (waiting for Sentry/Datadog access)
    {
        "session_id": "sess-hh8888",
        "issue_id": 8,
        "status": "pending",
        "created_at": _ts(2, 6, 5),
        "completed_at": None,
        "cost_usd": None,
        "session_url": "https://app.devin.ai/sessions/sess-hh8888",
        "pr_number": None,
        "commits_count": None,
        "ci_first_pass": None,
        "human_intervened": None,
        "duration_seconds": None,
        "pr_merged": None,
        "status_detail": "waiting_for_user",
    },
    # issue 9 — no session (ambiguous, declined)
    # (issue 10 only)
    # issue 10 — running right now
    {
        "session_id": "sess-jj0000",
        "issue_id": 10,
        "status": "running",
        "created_at": _ts(3, 2, 5),
        "completed_at": None,
        "cost_usd": None,
        "session_url": "https://app.devin.ai/sessions/sess-jj0000",
        "pr_number": None,
        "commits_count": None,
        "ci_first_pass": None,
        "human_intervened": None,
        "duration_seconds": None,
        "pr_merged": None,
        "status_detail": "working",
    },
]

# ---------------------------------------------------------------------------
# Mock events
# ---------------------------------------------------------------------------

def _build_events() -> list[dict]:
    events = []
    eid = 1

    def evt(ts, event_type, issue_id=None, session_id=None, pr_number=None, payload=None, key=None):
        nonlocal eid
        events.append({
            "id": eid,
            "timestamp": ts,
            "event_type": event_type,
            "issue_id": issue_id,
            "session_id": session_id,
            "pr_number": pr_number,
            "payload": json.dumps(payload) if payload else None,
            "idempotency_key": key or f"{event_type}-{eid}",
        })
        eid += 1

    # Issues created (day 0-3)
    for issue in ISSUES:
        evt(issue["created_at"], "issue.created", issue_id=issue["issue_id"],
            key=f"issue.created-{issue['issue_id']}")

    # Sessions + associated events
    for sess in SESSIONS:
        sid = sess["session_id"]
        iid = sess["issue_id"]
        evt(sess["created_at"], "session.started", issue_id=iid, session_id=sid,
            key=f"session.started-{sid}")

        if sess["status"] == "completed":
            if sess["pr_number"]:
                pr_ts = _ts_between(sess["created_at"], sess["completed_at"], 0.6)
                evt(pr_ts, "pr.opened", issue_id=iid, session_id=sid,
                    pr_number=sess["pr_number"], key=f"pr.opened-{sid}")
                evt(sess["completed_at"], "pr.merged", issue_id=iid, session_id=sid,
                    pr_number=sess["pr_number"], key=f"pr.merged-{sid}")
            evt(sess["completed_at"], "session.completed", issue_id=iid, session_id=sid,
                payload={"cost_usd": sess["cost_usd"]}, key=f"session.completed-{sid}")

        elif sess["status"] == "failed":
            evt(sess["completed_at"], "session.failed", issue_id=iid, session_id=sid,
                key=f"session.failed-{sid}")

        elif sess["status"] == "blocked":
            evt(sess["completed_at"], "session.blocked", issue_id=iid, session_id=sid,
                payload={"reason": sess["status_detail"]}, key=f"session.blocked-{sid}")

        elif sess["status"] == "pending":
            pending_ts = _ts_between(sess["created_at"],
                                     _ts(4, 0),  # now-ish
                                     0.4)
            evt(pending_ts, "session.pending", issue_id=iid, session_id=sid,
                payload={"status_detail": sess["status_detail"]},
                key=f"session.pending-{sid}-{sess['status_detail']}")

    return events


def _ts_between(start_iso: str, end_iso: str, fraction: float) -> str:
    fmt = "%Y-%m-%dT%H:%M:%S+00:00"
    # Handle various ISO formats
    def _parse(s):
        for fmt in ("%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return datetime.fromisoformat(s)

    start = _parse(start_iso)
    end = _parse(end_iso)
    mid = start + (end - start) * fraction
    return mid.isoformat()


# ---------------------------------------------------------------------------
# DB insertion
# ---------------------------------------------------------------------------

def _wipe(db) -> None:
    for table in ("events", "sessions", "issues"):
        try:
            db.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    db.commit()


def _insert_issues(db, issues: list[dict]) -> None:
    db.executemany(
        """INSERT INTO issues
           (issue_id, title, complexity, source, severity, state,
            created_at, closed_at, reopened_at)
           VALUES
           (:issue_id, :title, :complexity, :source, :severity, :state,
            :created_at, :closed_at, :reopened_at)""",
        issues,
    )
    db.commit()


def _insert_sessions(db, sessions: list[dict]) -> None:
    db.executemany(
        """INSERT INTO sessions
           (session_id, issue_id, status, created_at, completed_at, cost_usd,
            session_url, pr_number, commits_count, ci_first_pass, human_intervened,
            duration_seconds, pr_merged, status_detail)
           VALUES
           (:session_id, :issue_id, :status, :created_at, :completed_at, :cost_usd,
            :session_url, :pr_number, :commits_count, :ci_first_pass, :human_intervened,
            :duration_seconds, :pr_merged, :status_detail)""",
        sessions,
    )
    db.commit()


def _insert_events(db, events: list[dict]) -> None:
    db.executemany(
        """INSERT OR IGNORE INTO events
           (id, timestamp, event_type, issue_id, session_id, pr_number,
            payload, idempotency_key)
           VALUES
           (:id, :timestamp, :event_type, :issue_id, :session_id, :pr_number,
            :payload, :idempotency_key)""",
        events,
    )
    db.commit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(db_path: str | None = None) -> int:
    if db_path is None:
        try:
            cfg = load_config()
            db_path = cfg.db_path
        except Exception:
            db_path = "cognition.db"

    logger.info("mock_data.start", extra={"db_path": db_path})

    db = get_db(db_path)

    _wipe(db)
    logger.info("mock_data.wiped")

    _insert_issues(db, ISSUES)
    logger.info("mock_data.issues_inserted", extra={"count": len(ISSUES)})

    _insert_sessions(db, SESSIONS)
    logger.info("mock_data.sessions_inserted", extra={"count": len(SESSIONS)})

    events = _build_events()
    _insert_events(db, events)
    logger.info("mock_data.events_inserted", extra={"count": len(events)})

    db.close()

    print(f"Mock data written to {db_path}")
    print(f"  {len(ISSUES)} issues | {len(SESSIONS)} sessions | {len(events)} events")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed mock data for dashboard development")
    parser.add_argument("--db", dest="db_path", default=None, help="Path to SQLite DB")
    args = parser.parse_args()
    raise SystemExit(main(db_path=args.db_path))
