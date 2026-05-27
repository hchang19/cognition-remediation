# System Design — Event-Driven Vulnerability Remediation

---

## Problem Statement

Build a working automation that:
1. Creates structured vulnerability/remediation issues in a fork of `apache/superset`
2. Listens for issue events and dispatches Devin sessions to remediate them
3. Tracks each session's progress, output, and outcome
4. Surfaces an observability layer answering: *"if I were an engineering leader, how would I know this is working?"*

---

## Scoping Principles

| Principle | Demo | Future |
|---|---|---|
| Infrastructure | Single process + SQLite, runs on a laptop | Kafka backbone, Postgres, Redis, separate workers |
| Issue discovery | Pre-seeded pip-audit CVEs + manual issues | Full scanner suite (Bandit, Semgrep, Dependabot) on a cron |
| Event ingestion | Webhook (ngrok) + polling fallback | Dedicated event bus, multiple consumers |
| Metrics | SQL at read time | Stream-computed aggregates (Flink/Spark) |
| Session tracking | Poll Devin API every 30s | Devin webhooks |
| Scale | Single fork, single-digit concurrent sessions | Multi-repo, multi-tenant, multi-agent comparison |

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│              seed_issues.py (one-shot)               │
│  Pre-selected pip-audit CVEs + manual spectrum       │
│  issues → GitHub REST API                            │
└──────────────────────┬──────────────────────────────┘
                       │ creates labeled issues
                       ▼
┌─────────────────────────────────────────────────────┐
│           Apache Superset Fork (GitHub)              │
│  Issues labeled `auto-remediate` + complexity tags   │
└──────────────────────┬──────────────────────────────┘
                       │ webhook on issue.opened / polling fallback
                       ▼
┌─────────────────────────────────────────────────────┐
│       Orchestrator (FastAPI, single process)         │
│                                                      │
│  POST /webhook     GitHub event receiver             │
│  GET  /healthz     Liveness check                    │
│                                                      │
│  orchestrator.py   Issue → Devin routing logic       │
│                    Blocks ambiguous, gates on cost    │
│                                                      │
│  poller.py         Background thread                 │
│                    - Devin session status (30s)       │
│                    - PR commits + CI status (5min)    │
│                                                      │
│  devin_client.py   Devin API wrapper                 │
│  github_client.py  PyGithub wrapper                  │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│           SQLite  (cognition.db, gitignored)         │
│  events    — append-only source of truth             │
│  sessions  — Devin session state + metrics           │
│  issues    — issue lifecycle state                   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│         Streamlit Dashboard (dashboard/app.py)        │
│  KPI strip · timeline · per-issue table              │
│  Segmented by complexity label                       │
└─────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. Issue Seeder

**Demo:** `scripts/seed_issues.py` reads `config/issues.yml` and creates issues directly via GitHub API. No scanner infrastructure.

Issue set covers four types across the complexity spectrum:

| Complexity | Types included | Count |
|---|---|---|
| `definite` | CVE vulnerability, version upgrade, specific bug with reproducer | 3 |
| `semi-definite` | User-reported bug (location unknown), feature request | 2 |
| `ambiguous` | Vague ask — orchestrator declines, routes to human | 1 |

`complexity` drives orchestrator routing. `type` (`vulnerability`, `upgrade`, `bug`, `feature`, `ambiguous`) is for dashboard segmentation. All issues include a `<!-- cognition-meta {...} -->` HTML comment carrying `complexity`, `type`, `source`, and `idempotency_key` for dedup and orchestrator parsing.

See [docs/stage-1-seeder.md](./docs/stage-1-seeder.md) for the full issue set and `config/issues.yml` schema.

**Future:** GitHub Action on a daily schedule running pip-audit + Bandit + Semgrep. `scan_to_issues.py` converts findings with a `(source, rule_id, file, line)` hash for idempotency.

---

### 2. Orchestrator

The only persistent process. Three responsibilities:

**Webhook receiver (`webhook.py`)**

Accepts `POST /webhook` from GitHub. Verifies `X-Hub-Signature-256` using `GITHUB_WEBHOOK_SECRET`.

| Event | Action |
|---|---|
| `issues.opened` + label `auto-remediate` | Insert `issue.created` event → route to orchestrator |
| `pull_request.opened` by Devin author | Insert `pr.opened` event |
| `issues.reopened` | Insert `issue.reopened` event |

Falls back to polling new `auto-remediate` issues every 60s if `GITHUB_WEBHOOK_SECRET` is unset.

**Orchestrator logic (`orchestrator.py`)**

Routes issues based on `complexity` label:

| Complexity | Action |
|---|---|
| `definite` | Create Devin session immediately |
| `semi-definite` | Create Devin session with investigation-focused prompt |
| `ambiguous` | Skip — label `needs-human-scoping`, log `session.declined` event |

Pre-flight checks before any session creation:
- Daily session count < `DEVIN_DAILY_SESSION_LIMIT`
- `PAUSE` env var not set
- No existing open session for this issue (idempotency)

**Future:** Per-issue cost budget, severity-aware routing (`severity:critical` pages human directly), multi-agent dispatch (same issue → Devin + Claude Code for comparison).

**Background poller (`poller.py`)**

Single thread, two loops:
- Every 30s: poll `get_session()` for each non-terminal session. On state change → insert event. Terminal states: `completed`, `failed`, `blocked`.
- Every 5min: for each open Devin-authored PR, fetch commits + CI run status. Detect human commits → insert `pr.human_commit` event.

`blocked` is treated as a first-class terminal state — distinct from `failed`. It means Devin hit something it couldn't resolve without human input. Surfaced separately on the dashboard as "needs human" rather than grouped with failures.

**Future:** Replace polling with Devin webhooks if available. Move loops to separate worker processes for scale.

---

### 3. Devin Integration

Every remediation runs as a Devin session. The orchestrator's job is to scope work clearly, dispatch it, and measure what comes back.

#### Session Lifecycle

```
issue.created
    │
    ▼
orchestrator.py routes by complexity
    │
    ├── ambiguous → session.declined (label: needs-human-scoping)
    │
    └── definite / semi-definite
            │
            ▼
        devin_client.create_session(prompt, repo_url, issue_id)
            │
            ▼
        status: running ──────────────── poller polls every 30s
            │
            ├── blocked  → session.blocked  (Devin needs human input)
            ├── failed   → session.failed   (Devin could not complete)
            └── completed → session.completed
                    │
                    ▼
                PR opened on fork
                    │
                    ├── CI runs → pr.ci_completed (pass / fail)
                    ├── Human pushes → pr.human_commit
                    └── Issue closed → issue.closed / issue.reopened
```

`blocked` and `failed` are distinct terminal states:
- `blocked` — Devin reached a decision point requiring human input (e.g. ambiguous acceptance criteria, missing credentials). Routed to human queue.
- `failed` — Devin attempted the task and could not produce a valid output.

#### Devin Client (`devin_client.py`)

```python
create_session(prompt: str, repo_url: str, issue_id: int) -> str   # session_id
get_session(session_id: str) -> SessionResponse
```

`SessionResponse` fields captured and written to `sessions` table:

| Field | Source | Used for |
|---|---|---|
| `status` | Devin API | Session lifecycle, terminal state detection |
| `cost_usd` | Devin API | Cost per fix metric |
| `session_url` | Devin API | Dashboard link, session replay for demo |
| `pr_url` | Devin API / GitHub | PR link in dashboard |
| `structured_output` | Devin API | Summary of what Devin did — shown in per-issue table |

Retry on transient failures: exponential backoff, 3 attempts, then log `session.start_failed`.

**Future:** Devin webhooks for push-based status updates instead of polling.

#### Prompt Templates (`prompts.py`)

Prompt quality determines Devin's success rate. One template per complexity tier, versioned in code.

**`definite` prompt:**
```
You are remediating a security vulnerability in apache/superset.

Issue: {issue_title}
File: {file_path}
Location: line {line_number}
CVE / Rule: {rule_id}

Remediation:
{suggested_remediation}

Acceptance criteria:
{acceptance_criteria}

Instructions:
1. Create a branch named fix/{issue_number}-{slug}
2. Make only the changes required by the acceptance criteria
3. Do not refactor surrounding code
4. Run existing tests — do not modify them unless the issue explicitly requires it
5. Open a PR referencing this issue with a clear description of what changed and why
```

**`semi-definite` prompt:**
```
You are investigating and remediating a reported issue in apache/superset.

Issue: {issue_title}
Reported behavior: {issue_body}
Suspected location: {suspected_location}

Instructions:
1. Read the issue carefully before touching any code
2. Locate the root cause — document your finding in the PR description before implementing a fix
3. If the fix requires a design decision, open a follow-up issue describing the options instead of choosing unilaterally
4. Create a branch named fix/{issue_number}-{slug}
5. Open a PR with: root cause analysis, what you changed, and any open questions
```

**`ambiguous` prompt:** Not used — ambiguous issues are declined before a session is created. The orchestrator labels them `needs-human-scoping` and logs `session.declined`.

**Future:** A/B test prompt variants per issue type. Use LLM to classify Devin's structured output for richer failure analysis.

---

### 4. SQLite Schema

Three tables. All writes are appends or status updates — no deletes.

**`events`** — append-only, source of truth for all metrics

```sql
id              INTEGER PRIMARY KEY
timestamp       TEXT          -- ISO 8601
event_type      TEXT          -- see event taxonomy below
issue_id        INTEGER
session_id      TEXT
pr_number       INTEGER
payload         TEXT          -- raw JSON
idempotency_key TEXT UNIQUE   -- e.g. session_id + ":" + status
```

Event taxonomy:
```
issue.created       issue.closed        issue.reopened
session.started     session.completed   session.failed
session.blocked     session.declined    session.start_failed
pr.opened           pr.human_commit     pr.ci_completed
```

**`sessions`** — current Devin session state

```sql
session_id         TEXT PRIMARY KEY
issue_id           INTEGER
status             TEXT    -- running / completed / failed / blocked
created_at         TEXT
completed_at       TEXT
cost_usd           REAL
session_url        TEXT    -- Devin session replay link
pr_number          INTEGER
commits_count      INTEGER -- populated after PR opened
ci_first_pass      INTEGER -- 0 / 1 / NULL (first CI run result)
human_intervened   INTEGER -- 0 / 1 / NULL
duration_seconds   INTEGER -- completed_at - created_at
```

Note: `declined` issues never produce a session row — only an `events` entry with `event_type = session.declined` and no `session_id`.

**`issues`** — current issue state

```sql
issue_id      INTEGER PRIMARY KEY
title         TEXT
complexity    TEXT    -- definite / semi-definite / ambiguous
source        TEXT    -- pip-audit / manual
severity      TEXT
state         TEXT    -- open / closed / reopened
created_at    TEXT
closed_at     TEXT
reopened_at   TEXT
```

---

### 5. Dashboard (`dashboard/app.py`)

`streamlit run dashboard/app.py` — queries SQLite on every refresh.

**Top strip (KPIs):**
- Total issues · Sessions started · PRs opened · Success rate · Total cost · Blocked count

**Session timeline:**
- Line chart: issues created / sessions started / PRs opened by day

**Complexity breakdown:**
- Bar chart: success rate segmented by `complexity` label (definite / semi / ambiguous)

**Per-issue detail table:**
- Issue title · Complexity · Session status · Session URL · PR link · CI pass · Commits · Human intervened · Duration · Cost · Agent efficiency score

**Future:** Hosted dashboard with real-time WebSocket updates, DORA metrics once a deploy pipeline exists, multi-repo aggregation.

---

## Metric Collection

| Metric | Source |
|---|---|
| Sessions active / completed / failed / blocked | `sessions.status` |
| CI pass rate (first attempt) | `sessions.ci_first_pass` |
| Commits per issue | `sessions.commits_count` |
| Human intervention rate | `sessions.human_intervened` / total sessions |
| Agent cycle time | `sessions.duration_seconds` |
| Cost per fix | `sessions.cost_usd` where `status = completed` |
| Reopened rate | `issue.reopened` events / `issue.closed` events |
| Agent efficiency score | `(1 / commits) * ci_first_pass * (1 - reopened) * (1 - human_intervened)` |
| Throughput | Event counts grouped by day |
| All above segmented by complexity | Filter on `issues.complexity` |

All computed as SQL at dashboard load — no precomputed aggregates.

---

## Fault Tolerance

| Failure | Mitigation |
|---|---|
| Orchestrator crash | Restart resumes from SQLite — poller picks up non-terminal sessions |
| Webhook missed | Polling fallback catches any issue the webhook missed |
| Devin API failure | Exponential backoff × 3, then `session.start_failed` event |
| GitHub rate limit | Well under 5000/hr at demo scale — sleep on 429/403 and retry |
| SQLite lock contention | WAL mode + single writer thread |

---

## Services

| Service | Port | Role |
|---|---|---|
| `orchestrator` | 8000 | FastAPI — webhook receiver, Devin dispatch, background poller |
| `dashboard` | 8501 | Streamlit — read-only, queries SQLite only |

Both mount the same `cognition.db` via a Docker named volume. Dashboard never writes to SQLite.

## File Layout

```
cognition_remediation/
├── Dockerfile                     # builds both services from same image
├── docker-compose.yml             # orchestrator + dashboard services + shared volume
├── requirements.txt
├── .env.example
│
├── app/                           # orchestrator (FastAPI backend)
│   ├── main.py                    # FastAPI entrypoint, starts poller thread
│   ├── webhook.py                 # POST /webhook handler
│   ├── orchestrator.py            # Issue routing + Devin session creation
│   ├── poller.py                  # Background thread: Devin + PR/CI polling
│   ├── devin_client.py            # Devin API wrapper
│   ├── github_client.py           # GitHub API wrapper
│   ├── db.py                      # SQLite setup (WAL, schema init)
│   ├── events.py                  # Append-only event inserts + idempotency
│   ├── prompts.py                 # Devin prompt templates per complexity tier
│   └── shared/                    # shared across app + scripts
│       ├── config.py              # env var loading + validation
│       ├── github_session.py      # pre-authenticated requests.Session
│       ├── retry.py               # exponential backoff decorator
│       └── logger.py              # structured JSON logger
│
├── dashboard/                     # Streamlit frontend (read-only)
│   └── app.py
│
├── scripts/                       # one-shot ops, run via docker compose run
│   ├── seed_issues.py             # seeds issues into the GitHub fork
│   └── reset_demo.py              # wipes DB + closes test issues
│
└── config/
    └── issues.yml                 # issue definitions for the seeder
```

---

## Future Extensions

| Capability | What it requires |
|---|---|
| Full scanner suite | GitHub Action + pip-audit/Bandit/Semgrep + `scan_to_issues.py` |
| DORA metrics | Production deploy pipeline wired to the event log |
| Real-time dashboard | WebSocket or SSE from FastAPI → Streamlit |
| Multi-repo | Per-repo rate budgets, tenant isolation in schema |
| Multi-agent benchmarking | Same issue dispatched to Devin + Claude Code, comparison table |
| LLM failure classification | Replace `status` grouping with LLM-categorized failure reasons |
| Cost anomaly detection | Alert when cost/fix spikes or success rate drops |
| Severity-aware routing | `critical` issues page human directly, skip orchestrator |
