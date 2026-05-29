"""SQLite connection, pragmas, and schema creation.

All reads and writes in the system flow through this layer. Every connection
opened via ``get_db`` enables WAL journaling and foreign keys, and ensures the
schema exists.

The schema is defined inline as ``_SCHEMA_SQL`` and matches
``docs/stage-2-db.md`` exactly. Boolean-like columns are stored as ``INTEGER``
(0 / 1 / NULL) since SQLite has no native boolean type.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.shared.logger import get_logger

logger = get_logger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS issues (
    issue_id      INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    complexity    TEXT NOT NULL,
    source        TEXT NOT NULL,
    severity      TEXT,
    state         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    closed_at     TEXT,
    reopened_at   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id         TEXT PRIMARY KEY,
    issue_id           INTEGER NOT NULL,
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    cost_usd           REAL,
    session_url        TEXT,
    pr_number          INTEGER,
    commits_count      INTEGER,
    ci_first_pass      INTEGER,
    human_intervened   INTEGER,
    duration_seconds   INTEGER,
    pr_merged          INTEGER,
    FOREIGN KEY (issue_id) REFERENCES issues(issue_id)
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    issue_id        INTEGER,
    session_id      TEXT,
    pr_number       INTEGER,
    payload         TEXT,
    idempotency_key TEXT UNIQUE,
    FOREIGN KEY (issue_id) REFERENCES issues(issue_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Enable WAL journaling and foreign keys on the connection.

    WAL mode persists at the database file level once set, but issuing the
    PRAGMA on every open is cheap and guarantees we never silently fall back to
    rollback journaling on a fresh file. ``foreign_keys`` is a per-connection
    setting and MUST be set every time.
    """
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not yet exist."""
    with conn:
        conn.executescript(_SCHEMA_SQL)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental column additions to existing databases."""
    for stmt in [
        "ALTER TABLE sessions ADD COLUMN pr_merged INTEGER",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists


def get_db(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection at ``db_path`` and ensure the schema exists.

    Caller manages lifecycle (close). Pass ``":memory:"`` for tests.

    **Threading contract:** ``check_same_thread=False`` is set so the connection
    can be passed between threads (e.g. the FastAPI request handler and the
    background poller). This does NOT make the connection thread-safe —
    ``sqlite3.Connection`` does not serialize concurrent ``execute``/``commit``
    calls. The caller is responsible for one of:

      1. Restricting writes to a single thread (the design's "single writer"
         convention), OR
      2. Wrapping mutations in a ``threading.Lock``, OR
      3. Opening a separate connection per thread.

    WAL journaling permits concurrent readers + one writer at the file level,
    so option (3) gives the best throughput when multiple threads need writes.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    _create_schema(conn)
    _migrate(conn)
    logger.info("db_opened", extra={"db_path": db_path})
    return conn
