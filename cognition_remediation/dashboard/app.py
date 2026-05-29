"""Streamlit read-only dashboard for the vulnerability remediation system.

Queries the SQLite database on every refresh. No background updates needed.

Run:
    streamlit run dashboard/app.py

Environment:
    DB_PATH — path to the SQLite database file (default: app/data/remediation.db)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Vulnerability Remediation Dashboard",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = str(
    Path(__file__).parent.parent / "app" / "data" / "remediation.db"
)


def _db_path() -> str:
    return os.environ.get("DB_PATH", _DEFAULT_DB_PATH)


def _get_conn() -> sqlite3.Connection | None:
    """Open a read-only SQLite connection; return None if the DB doesn't exist."""
    path = _db_path()
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _query_df(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Execute *sql* and return a DataFrame (empty if no rows)."""
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# KPI queries
# ---------------------------------------------------------------------------

def _load_kpis(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()

    total_issues = cur.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    sessions_started = cur.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    prs_opened = cur.execute(
        "SELECT COUNT(*) FROM sessions WHERE pr_number IS NOT NULL"
    ).fetchone()[0]

    terminal = cur.execute(
        "SELECT SUM(status='completed') as ok, COUNT(*) as total "
        "FROM sessions WHERE status IN ('completed','failed','blocked')"
    ).fetchone()
    completed = terminal[0] or 0
    terminal_total = terminal[1] or 0
    success_rate = (completed / terminal_total) if terminal_total > 0 else None

    total_cost_row = cur.execute(
        "SELECT SUM(cost_usd) FROM sessions WHERE status='completed'"
    ).fetchone()
    total_cost = total_cost_row[0] if total_cost_row[0] is not None else 0.0

    blocked = cur.execute(
        "SELECT COUNT(*) FROM sessions WHERE status='blocked'"
    ).fetchone()[0]

    return {
        "total_issues": total_issues,
        "sessions_started": sessions_started,
        "prs_opened": prs_opened,
        "success_rate": success_rate,
        "total_cost": total_cost,
        "blocked": blocked,
    }


# ---------------------------------------------------------------------------
# Chart queries
# ---------------------------------------------------------------------------

_TIMELINE_SQL = """
SELECT DATE(timestamp) AS day, event_type, COUNT(*) AS count
FROM events
WHERE event_type IN ('issue.created', 'session.started', 'pr.opened')
GROUP BY day, event_type
ORDER BY day
"""

_COMPLEXITY_SQL = """
SELECT i.complexity,
       SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) AS succeeded,
       COUNT(*) AS total
FROM sessions s
JOIN issues i ON s.issue_id = i.issue_id
WHERE s.status IN ('completed', 'failed', 'blocked')
GROUP BY i.complexity
"""

_DETAIL_SQL = """
SELECT
    i.title,
    i.complexity,
    s.status,
    s.session_url,
    s.pr_number,
    s.ci_first_pass,
    s.commits_count,
    s.human_intervened,
    s.duration_seconds,
    s.cost_usd,
    CASE WHEN s.commits_count > 0 AND s.status = 'completed'
         THEN ROUND(
                (1.0 / s.commits_count)
                * s.ci_first_pass
                * (1 - s.human_intervened)
                * (1 - CASE WHEN i.reopened_at IS NOT NULL THEN 1 ELSE 0 END),
              4)
         ELSE NULL END AS efficiency_score
FROM issues i
LEFT JOIN sessions s ON i.issue_id = s.issue_id
ORDER BY i.created_at DESC
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

st.title("Vulnerability Remediation Dashboard")

# Refresh button at the top
if st.button("Refresh"):
    st.rerun()

conn = _get_conn()

if conn is None:
    st.info(
        f"No database found at `{_db_path()}`. "
        "Run the seeder and orchestrator first, then refresh."
    )
    st.stop()

# ---- KPI strip ----------------------------------------------------------------

st.subheader("Overview")

kpis = _load_kpis(conn)

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.metric("Total Issues", kpis["total_issues"])

with col2:
    st.metric("Sessions Started", kpis["sessions_started"])

with col3:
    st.metric("PRs Opened", kpis["prs_opened"])

with col4:
    if kpis["success_rate"] is None:
        st.metric("Success Rate", "—")
    else:
        st.metric("Success Rate", f"{kpis['success_rate']:.0%}")

with col5:
    st.metric("Total Cost (USD)", f"${kpis['total_cost']:.2f}")

with col6:
    st.metric("Blocked", kpis["blocked"])

st.divider()

# ---- Session timeline -------------------------------------------------------

st.subheader("Session Timeline")

timeline_df = _query_df(conn, _TIMELINE_SQL)

if timeline_df.empty:
    st.info("No events recorded yet.")
else:
    # Pivot so each event_type becomes a column
    pivot = timeline_df.pivot_table(
        index="day", columns="event_type", values="count", aggfunc="sum"
    ).fillna(0)
    pivot.index = pd.to_datetime(pivot.index)
    pivot.columns.name = None  # cosmetic
    st.line_chart(pivot)

st.divider()

# ---- Complexity breakdown ---------------------------------------------------

st.subheader("Complexity Breakdown — Success Rate")

complexity_df = _query_df(conn, _COMPLEXITY_SQL)

if complexity_df.empty:
    st.info("No terminal sessions yet.")
else:
    complexity_df["success_rate"] = (
        complexity_df["succeeded"] / complexity_df["total"]
    ).round(4)
    chart_df = complexity_df.set_index("complexity")[["success_rate"]]
    st.bar_chart(chart_df)

st.divider()

# ---- Per-issue detail table ------------------------------------------------

st.subheader("Per-Issue Detail")

detail_df = _query_df(conn, _DETAIL_SQL)

if detail_df.empty:
    st.info("No issues found.")
else:
    # Build clickable columns using Streamlit column config
    column_config: dict = {
        "title": st.column_config.TextColumn("Issue Title"),
        "complexity": st.column_config.TextColumn("Complexity"),
        "status": st.column_config.TextColumn("Status"),
        "ci_first_pass": st.column_config.CheckboxColumn("CI Pass"),
        "human_intervened": st.column_config.CheckboxColumn("Human Intervened"),
        "commits_count": st.column_config.NumberColumn("Commits"),
        "duration_seconds": st.column_config.NumberColumn("Duration (s)"),
        "cost_usd": st.column_config.NumberColumn("Cost (USD)", format="$%.4f"),
        "efficiency_score": st.column_config.NumberColumn(
            "Efficiency Score", format="%.4f"
        ),
    }

    # session_url — render as link if present
    if "session_url" in detail_df.columns:
        column_config["session_url"] = st.column_config.LinkColumn(
            "Session URL",
            display_text="Open",
        )

    # pr_number — build a URL from the env var GITHUB_REPO, fall back to plain number
    github_repo = os.environ.get("GITHUB_REPO", "")
    if "pr_number" in detail_df.columns and github_repo:
        detail_df["pr_link"] = detail_df["pr_number"].apply(
            lambda n: f"https://github.com/{github_repo}/pull/{int(n)}"
            if pd.notna(n)
            else None
        )
        column_config["pr_link"] = st.column_config.LinkColumn(
            "PR",
            display_text="#{0}",
        )
        # Show pr_link instead of pr_number
        display_cols = [c for c in detail_df.columns if c != "pr_number"]
        detail_df = detail_df[display_cols]
    else:
        # No repo configured — show pr_number as plain text
        if "pr_number" in detail_df.columns:
            column_config["pr_number"] = st.column_config.NumberColumn("PR #")

    st.dataframe(detail_df, use_container_width=True, column_config=column_config)

conn.close()
