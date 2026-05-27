# Stage 2 — Database + Event Layer

All reads and writes flow through this layer. Build before stage 4 (orchestrator) and stage 5 (dashboard).

## Files

```
app/db.py       # connection, schema creation, WAL setup
app/events.py   # typed event insert helpers
```

## SQLite Setup (`db.py`)

- Single file: `cognition.db` (gitignored)
- `PRAGMA journal_mode=WAL` on connection open
- All writes go through a single writer thread — no concurrent writes
- `get_db()` returns a connection; caller manages lifecycle

## Schema

```sql
CREATE TABLE events (
    id              INTEGER PRIMARY KEY,
    timestamp       TEXT NOT NULL,          -- ISO 8601
    event_type      TEXT NOT NULL,
    issue_id        INTEGER,
    session_id      TEXT,
    pr_number       INTEGER,
    payload         TEXT,                   -- raw JSON
    idempotency_key TEXT UNIQUE             -- prevents duplicate events
);

CREATE TABLE sessions (
    session_id         TEXT PRIMARY KEY,
    issue_id           INTEGER NOT NULL,
    status             TEXT NOT NULL,       -- running / completed / failed / blocked / declined
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    cost_usd           REAL,
    session_url        TEXT,               -- Devin session replay link
    pr_number          INTEGER,
    commits_count      INTEGER,
    ci_first_pass      INTEGER,            -- 0 / 1 / NULL
    human_intervened   INTEGER,            -- 0 / 1 / NULL
    duration_seconds   INTEGER
);

CREATE TABLE issues (
    issue_id      INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    complexity    TEXT NOT NULL,           -- definite / semi-definite / ambiguous
    source        TEXT NOT NULL,           -- pip-audit / manual
    severity      TEXT,
    state         TEXT NOT NULL,           -- open / closed / reopened
    created_at    TEXT NOT NULL,
    closed_at     TEXT,
    reopened_at   TEXT
);
```

## Event Types (`events.py`)

Typed insert helper for each event type. All inserts use `INSERT OR IGNORE` on `idempotency_key`.

```
issue.created
issue.closed
issue.reopened
session.started
session.completed
session.failed
session.blocked
session.declined
session.start_failed
pr.opened
pr.human_commit
pr.ci_completed
```

Each helper signature:
```python
def insert_event(db, event_type: str, idempotency_key: str, **kwargs) -> None
```

`kwargs` maps to nullable columns: `issue_id`, `session_id`, `pr_number`, `payload` (dict → JSON).
