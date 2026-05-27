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
