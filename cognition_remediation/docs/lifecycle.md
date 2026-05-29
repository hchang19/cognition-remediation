# System Lifecycle and State Reference

End-to-end description of how an issue moves through the remediation pipeline — from open to closed — with every state, event, and transition documented.

---

## Overview

```
GitHub Issue (auto-remediate)
        │
        ▼
  Orchestrator (handle_issue)
        │
        ├─ ambiguous ──────────────────► needs-human-scoping label (terminal)
        │
        ├─ semi-definite ──────────────► Devin session (investigate + fix)
        │                                        │
        └─ definite ──────────────────► Devin session (surgical fix)
                                                 │
                                         Devin opens PR
                                                 │
                                         CI runs on PR
                                                 │
                                   ┌─────────────┴─────────────┐
                               CI pass                      CI fail
                                   │                           │
                           (auto-merge gate)              PR stays open
                                   │
                         merge_eligible_prs()
                                   │
                         PR merged + issue closed
```

---

## Issue Lifecycle

| State | Meaning | How entered |
|---|---|---|
| `open` | Newly filed, not yet dispatched | GitHub issue created with `auto-remediate` label |
| `dispatched` | Devin session created | `handle_issue()` calls `devin.create_session()` |
| `declined` | Too ambiguous; sent to human | `complexity:ambiguous` label detected; `needs-human-scoping` added |
| `in_progress` | Devin is working | Session status = `running` |
| `review` | Devin opened a PR | Poller or webhook detects PR opened by Devin |
| `merged` | PR merged and issue closed | `merge_eligible_prs()` calls `gh.merge_pr()` + `gh.close_issue()` |
| `closed` | Manually closed or reset | `reset_demo.py` or human action |

GitHub auto-closes the issue on merge if Devin includes `Closes #N` in the PR description (both prompts instruct this). `demo.py --auto-merge` also calls `gh.close_issue()` explicitly as a fallback.

---

## Session Lifecycle

Sessions track a single Devin run for one issue.

```
       create_session()
            │
            ▼
         running  ◄──── poller checks every 30s
            │
     ┌──────┼──────┐
     ▼      ▼      ▼
completed failed blocked
```

| Status | Meaning |
|---|---|
| `running` | Devin is actively working; session URL is live |
| `completed` | Devin finished and (usually) opened a PR |
| `failed` | Devin session errored out; no PR |
| `blocked` | Devin needs human input to continue |

Terminal statuses: `completed`, `failed`, `blocked`. The poller stops polling a session once it reaches a terminal status.

**Fields populated on terminal transition:**

| Field | Set when |
|---|---|
| `completed_at` | Terminal status reached |
| `cost_usd` | Devin API response on terminal status |
| `session_url` | First non-running status |
| `pr_number` | PR opened by Devin (webhook or poller detects) |
| `ci_first_pass` | PR CI run completes (PR poller, every 5min) |
| `human_intervened` | A non-Devin commit appears on the PR branch |
| `pr_merged` | `merge_eligible_prs()` successfully merges the PR |

---

## PR Lifecycle

| State | Meaning | How detected |
|---|---|---|
| `open` | Devin opened; CI running | Webhook (`pull_request.opened`) or poller |
| `ci_running` | GitHub Actions in progress | `get_latest_ci_run()` returns `status=in_progress` |
| `ci_passed` | All checks green | `ci_first_pass=1` written to `sessions` |
| `ci_failed` | Checks failed | `ci_first_pass=0` written to `sessions` |
| `merged` | Squash-merged via API | `pr_merged=1`, issue closed |
| `closed` | Closed without merging | `reset_demo.py` on teardown |

---

## Event Taxonomy

All events are written to the `events` table. `issue_id` is always set; `session_id` is set when the event is session-scoped.

| Event type | Trigger | Session-scoped |
|---|---|---|
| `issue.created` | `handle_issue()` begins processing a new issue | No |
| `issue.reopened` | Webhook receives `issues.reopened` | No |
| `session.started` | `devin.create_session()` succeeds | Yes |
| `session.declined` | `complexity:ambiguous` — orchestrator skips | No |
| `session.completed` | Poller observes Devin status → `completed` | Yes |
| `session.failed` | Poller observes Devin status → `failed` | Yes |
| `session.blocked` | Poller observes Devin status → `blocked` | Yes |
| `pr.opened` | Webhook detects PR opened by Devin author | Yes |
| `pr.human_commit` | PR poller finds a non-Devin commit on the branch | Yes |
| `pr.ci_completed` | PR poller: CI run reaches `completed` status | Yes |

---

## Trigger Conditions

### Orchestrator: `handle_issue()`

Called by the webhook (`BackgroundTasks`) on `issues.opened` with `auto-remediate` label, or by the poller fallback every 60s when `GITHUB_WEBHOOK_SECRET` is unset.

Pre-flight aborts (no session created):

| Condition | Why |
|---|---|
| `PAUSE` env var set | Manual circuit breaker |
| Daily session count ≥ `DEVIN_DAILY_SESSION_LIMIT` | Cost cap |
| Non-terminal session already exists for this `issue_id` | Idempotency |

Routing by `complexity:*` label:

| Label | Action |
|---|---|
| `complexity:definite` | `create_session(definite_prompt)` |
| `complexity:semi-definite` | `create_session(semi_definite_prompt)` |
| `complexity:ambiguous` or missing | Add `needs-human-scoping`, emit `session.declined` |

### Session Poller (every 30s)

For each `sessions` row where `status = 'running'`:
1. Call `devin.get_session(session_id)`
2. If status changed → emit event, update row
3. On terminal: capture `pr_number`, `cost_usd`, `session_url`, `completed_at`

### PR Poller (every 5 min)

For each `sessions` row where `status = 'completed'` and `pr_number IS NOT NULL`:
1. Fetch commits — if any author doesn't contain "devin" (case-insensitive) → `pr.human_commit`, set `human_intervened=1`
2. Fetch latest CI run — if `completed` and not yet logged → `pr.ci_completed`, set `ci_first_pass`

### Auto-Merge Gate (`demo.py --auto-merge`)

`merge_eligible_prs()` queries for rows satisfying all of:

| Column | Condition |
|---|---|
| `sessions.status` | `= 'completed'` |
| `issues.complexity` | `= 'definite'` |
| `sessions.ci_first_pass` | `= 1` |
| `sessions.human_intervened` | `= 0` or `NULL` |
| `sessions.pr_merged` | `IS NULL` |
| `sessions.pr_number` | `IS NOT NULL` |

On match: squash-merge via `gh.merge_pr()`, set `pr_merged=1`, then close issue via `gh.close_issue()`.

### Reset (`reset_demo.py`)

Full teardown — run between demo runs:

1. Fetch open PRs on `fix/` branches → close each PR, delete its branch
2. Fetch open `auto-remediate` issues → close each issue
3. `DELETE FROM events, sessions, reviewer_sessions, issues`

---

## State Persistence

All state lives in SQLite (`cognition.db`). On orchestrator restart, the session poller resumes from all rows where `status = 'running'` — no in-flight sessions are lost.

The event log is append-only. The dashboard and metrics table read directly from `events` + `sessions` joins; no derived state is cached outside SQLite.

---

## Known Gaps

See [stage-4-orchestrator.md § Known Failure Points](stage-4-orchestrator.md#known-failure-points) for documented gaps. The most operationally significant:

- No session termination when an issue is manually closed mid-run
- Retry on Devin 5xx can create ghost sessions (second session on retry fires)
- Webhook + poller race condition on concurrent `handle_issue()` calls
