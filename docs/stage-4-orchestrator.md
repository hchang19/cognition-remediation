# Stage 4 — Orchestrator

The only persistent process. FastAPI app with a background poller thread.

## Files

```
app/main.py          # FastAPI entrypoint, starts background thread
app/webhook.py       # POST /webhook handler
app/orchestrator.py  # Issue routing + session creation logic
app/poller.py        # Background polling thread
app/prompts.py       # Devin prompt templates per complexity tier
```

---

## `app/main.py`

- Mounts `webhook.py` router
- On startup: starts `poller.py` as a daemon thread
- Exposes `GET /healthz` → `{"status": "ok"}`

---

## `app/webhook.py`

`POST /webhook` — receives GitHub event payloads.

**Signature verification:** `X-Hub-Signature-256` using `GITHUB_WEBHOOK_SECRET`. Reject with 403 on invalid signature. If `GITHUB_WEBHOOK_SECRET` is unset, the webhook endpoint is not registered — issue pickup falls back to the poller instead.

**Event handling:**

| GitHub event | Action |
|---|---|
| `issues.opened` + has label `auto-remediate` | `insert_event(issue.created)` → `orchestrator.handle_issue(issue)` in `BackgroundTasks` |
| `pull_request.opened`, author matches Devin | `insert_event(pr.opened)`, update `sessions.pr_number` |
| `issues.reopened` | `insert_event(issue.reopened)`, update `issues.reopened_at` |

Respond `200` immediately. All Devin API calls happen in `BackgroundTasks` — never block the webhook response.

**Polling fallback:** If `GITHUB_WEBHOOK_SECRET` is not set, `poller.py` polls for new `auto-remediate` issues every 60s instead.

---

## `app/orchestrator.py`

```python
def handle_issue(issue: Issue, db) -> None:
```

**Pre-flight checks** (abort if any fail):
1. `PAUSE` env var not set
2. Daily session count < `DEVIN_DAILY_SESSION_LIMIT`
3. No existing non-terminal session for this `issue_id`

**Routing by complexity:**

| `complexity` label | Action |
|---|---|
| `definite` | `create_session(definite_prompt(issue), ...)` |
| `semi-definite` | `create_session(semi_definite_prompt(issue), ...)` |
| `ambiguous` | `add_label(needs-human-scoping)`, `insert_event(session.declined)`, return |

On session creation: `insert_event(session.started)`, upsert `sessions` row with `status=running`.

---

## `app/prompts.py`

Two prompt templates. Rendered with issue fields before sending to Devin.

**`definite_prompt(issue)`** — prescriptive, surgical:
```
You are remediating a security vulnerability in apache/superset.

Issue: {title}
File: {file_path}
CVE / Rule: {rule_id}

Remediation: {suggested_remediation}

Acceptance criteria:
{acceptance_criteria}

Instructions:
- Branch name: fix/{issue_number}-{slug}
- Change only what the acceptance criteria require
- Do not refactor surrounding code
- Run existing tests without modifying them
- Open a PR referencing issue #{issue_number}
```

**`semi_definite_prompt(issue)`** — investigative first:
```
You are investigating and remediating a reported issue in apache/superset.

Issue: {title}
Reported behavior: {body}
Suspected location: {suspected_location}

Instructions:
- Read the full issue before touching code
- Document root cause in the PR description before implementing
- If a fix requires a design decision, open a follow-up issue instead of choosing unilaterally
- Branch name: fix/{issue_number}-{slug}
- Open a PR with: root cause, what changed, open questions
```

---

## `app/poller.py`

Single background thread. Two loops running in sequence with sleep between:

**Session poller (every 30s):**
```
For each session where status = 'running':
    response = get_session(session_id)
    if response.status != current status:
        insert_event(session.<new_status>)
        update sessions SET status, completed_at, cost_usd, session_url
        if terminal (completed / failed / blocked):
            fetch pr_number from response, update sessions
```

**PR poller (every 5min):**
```
For each session where status = 'completed' and pr_number is not null:
    commits = get_pr_commits(pr_number)
    if any commit author is not Devin:
        insert_event(pr.human_commit) if not already logged
        update sessions SET human_intervened = 1

    ci = get_latest_ci_run(pr_number)
    if ci.status = 'completed' and not yet logged:
        insert_event(pr.ci_completed, payload={conclusion})
        update sessions SET ci_first_pass = (conclusion == 'success')
```

On orchestrator restart: resumes from all non-terminal sessions in SQLite — no state lost.

---

## Known Failure Points

These are documented gaps, not bugs. They are acceptable at demo scale but would need hardening before production use.

### Duplicate Session Risk

**Retry-on-timeout creates ghost sessions on Devin.**
`devin.create_session()` is wrapped in `with_retry` (max 3 attempts). If Devin processes the request, creates the session internally, but returns a 5xx before the response arrives, the retry fires and creates a *second* session on Devin. Our DB only records the second session ID. The first session is running and billing with no record in our system.

**Race condition between webhook and poller.**
`handle_issue()` can be called from two places simultaneously: the webhook handler (FastAPI `BackgroundTasks`) and `_poll_issues()` in the poller thread. Both can pass the `_has_active_session()` check before either commits a sessions row. Result: two Devin sessions created for the same issue. There is no mutex or DB-level lock preventing this. Under normal load (webhook fires, then poller runs 60s later) the active session check prevents this — but if both fire within the same second, it can happen.

---

### Routing Correctness

**`severity:critical` is not blocked.**
`CLAUDE.md` specifies that `severity:critical` issues should skip the orchestrator and require human triage. The current orchestrator checks only the `complexity:*` label. An issue labelled `complexity:definite + severity:critical` gets dispatched to Devin rather than escalated.

**Label format drift causes silent decline.**
Complexity is detected with `l.startswith("complexity:")`. If the label is ever created as `complexity: definite` (with a space after the colon), the check fails and the issue defaults to `"ambiguous"`, triggering `needs-human-scoping` with no indication of why. Any label typo or format change silently reroutes everything.

**Multiple complexity labels produce non-deterministic routing.**
If an issue has both `complexity:definite` and `complexity:ambiguous` applied, `next()` picks whichever appears first when iterating the label set, which is unordered in Python. The routing outcome is non-deterministic and silently wrong.

---

### Data Integrity

**`INSERT OR IGNORE` silently drops body updates.**
`_upsert_issue()` uses `INSERT OR IGNORE`. If an issue was first inserted without a body (e.g. from an older code version that didn't store body), all subsequent calls with the real body are silently discarded. The reviewer prompt would see an empty body for that issue.

**Partial DB state on process crash between writes.**
`_upsert_issue()` and `INSERT INTO sessions` are separate `with db:` blocks. A crash between them leaves an `issues` row with an `issue.created` event but no session row. The next call correctly retries (no active session exists), but the duplicate `issue.created` event can skew event counts in the dashboard.

---

### Operational Blind Spots

**No circuit breaker on repeated Devin failures.**
If Devin's API is down, every issue on every poll cycle (60s) attempts session creation and fails after 3 retries. With 5 open issues this produces ~15 failed API calls per minute indefinitely. There is no backoff at the orchestrator level — only the per-call retry backoff. Alerts would have to be set on the `session.start_failed` event rate.

**`devin_daily_limit=0` is a silent no-op with no alerting.**
If `DEVIN_DAILY_SESSION_LIMIT` is accidentally set to `0`, the orchestrator logs `orchestrator.daily_limit_reached` for every single issue and does nothing. The system appears healthy — requests come in, the poller runs — but no sessions are ever created.

---

### Lifecycle Gaps

**No session termination when an issue is closed.**
If a GitHub issue is manually closed or resolved while a Devin session is running, the orchestrator does not call `devin.terminate_session()`. The session keeps running and billing until it reaches a terminal state on its own.

**No handling of label removal.**
If someone removes the `auto-remediate` label from an issue that already has a running session, the orchestrator does not notice. There is no mechanism to cancel an in-flight session when the dispatching precondition is removed.

---

### Configuration Assumptions

**`github_repo` must be `owner/repo`, not validated.**
`repo_url = f"https://github.com/{cfg.github_repo}"` assumes the value is formatted as `owner/repo`. A misconfigured value (full URL, trailing slash, org-only) produces a silently malformed repo URL that is passed to Devin. Devin will likely fail to clone and the session will either time out or produce an unhelpful error.
