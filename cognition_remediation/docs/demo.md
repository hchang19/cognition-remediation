# Demo Runbook

All commands run from `cognition_remediation/`.

---

## Prerequisites

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in GITHUB_TOKEN, GITHUB_REPO, DEVIN_API_KEY
```

---

## Command Reference

### Reset (hard stop)

Full teardown in one command. Terminates all active Devin sessions, closes
open PRs and issues on GitHub, then wipes the SQLite database. Safe to run
at any point in the demo lifecycle.

```bash
python3 scripts/reset_demo.py
```

**What it does, in order:**

| Step | Action | Why order matters |
|---|---|---|
| 1 | Terminate all `running`/`pending` Devin sessions via API | Must happen before DB wipe — session IDs live in SQLite |
| 2 | Close open `fix/` PRs + delete their branches | Devin sessions must be stopped first so Devin can't push new commits |
| 3 | Close open `auto-remediate` GitHub issues | |
| 4 | Wipe SQLite (`events`, `sessions`, `issues`) | Last — DB is source of truth until this step |

**Requirements:** `DEVIN_API_KEY` and `DEVIN_ORG_ID` must be set in `.env`
(required for session termination). If Devin credentials are missing or a
`terminate_session` call fails, the script logs a warning and continues —
GitHub and DB cleanup still run.

**After reset:** All issues are closed on GitHub. The seeder checks only
**open** issues for idempotency, so the next `demo.py` run re-creates them
with fresh issue numbers.

### Demo

```bash
python3 scripts/demo.py [options]
```

| Flag | Description |
|---|---|
| `--seed-delay N` | Wait N seconds after seeding before dispatch (allows GitHub to index labels) |
| `--dispatch-limit N` | Dispatch at most N issues — useful for sampling a subset |
| `--sandbox` | Fake Devin client: full orchestrator routing and DB writes, no API calls or cost |
| `--auto-merge` | After sessions complete, squash-merge `complexity:definite` PRs that pass CI with no human commits |
| `--no-seed` | Skip seeding — dispatch whatever is already open on GitHub |
| `--dry-run` | Skip dispatch and polling entirely — print current DB metrics only |
| `--verbose` / `-v` | Per-issue seed detail, per-session poll status with Devin URLs, per-PR CI breakdown |
| `--log` | Re-enable INFO structured JSON logs from all app modules on stderr |

---

## Typical Flows

### 1. Validate routing (no cost)

Tests the full orchestrator code path — seeding, routing by complexity, DB writes, event log — without creating real Devin sessions.

```bash
python3 scripts/reset_demo.py
python3 scripts/demo.py --sandbox --seed-delay 5 --verbose
python3 scripts/reset_demo.py
```

Expected output:
- `complexity:ambiguous` → **declined**, `needs-human-scoping` label added
- `complexity:definite` → **dispatched**, `definite_prompt` selected
- `complexity:semi-definite` → **dispatched**, `semi_definite_prompt` selected

### 2. Focused real run (2 issues)

Creates real Devin sessions for a small slice of the issue set.

```bash
python3 scripts/reset_demo.py
python3 scripts/demo.py --seed-delay 5 --dispatch-limit 2 --verbose
```

### 3. Full run with auto-merge

Seeds all 6 issues, dispatches all, polls until completion, then merges `complexity:definite` PRs that pass CI.

```bash
python3 scripts/reset_demo.py
python3 scripts/demo.py --seed-delay 5 --auto-merge --verbose
```

Auto-merge gate (all conditions must hold):
- `sessions.status = completed`
- `issues.complexity = definite`
- `sessions.ci_first_pass = 1`
- `sessions.human_intervened = 0` or NULL
- `sessions.pr_merged IS NULL`

### 4. Background run with log capture

```bash
python3 scripts/reset_demo.py
python3 scripts/demo.py --seed-delay 5 --log > demo.out 2> demo.log &
tail -f demo.log       # structured JSON from all app modules
tail -f demo.out       # human-readable progress output
```

### 5. Inspect current state (read-only)

```bash
python3 scripts/demo.py --dry-run --verbose
```

---

## Issue Set

Defined in `config/issues.yml`. Six issues across all complexity tiers:

| # | Title | Complexity | Type |
|---|---|---|---|
| 1 | Upgrade urllib3 (CVE-2023-45803) | `definite` | vulnerability |
| 2 | Upgrade Pillow ≥10.0.1 | `definite` | upgrade |
| 3 | `parse_human_datetime()` ValueError on empty input | `definite` | bug |
| 4 | Dashboard Unicode filter bug | `semi-definite` | bug |
| 5 | CSV export for SQL Lab | `semi-definite` | feature |
| 6 | Make dashboard rendering faster | `ambiguous` | ambiguous |

Issues #1–3 (`definite`) are eligible for auto-merge. Issues #4–5 (`semi-definite`) always require human review of the PR. Issue #6 (`ambiguous`) is declined by the orchestrator.

---

## Notes

- `--seed-delay 5` is the minimum recommended value — GitHub's label filter takes ~3–5 seconds to index newly created issues.
- After `reset_demo.py`, issues are closed on GitHub. The seeder checks only **open** issues for idempotency, so the next run re-creates them with fresh issue numbers.
- `--sandbox` is incompatible with `--dry-run`. Sandbox bypasses the dry-run gate to exercise the full dispatch path.
- See [lifecycle.md](lifecycle.md) for the full state machine and event taxonomy.
- See [stage-4-orchestrator.md](stage-4-orchestrator.md) for known failure points.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Seeder shows `skip` for all issues, fetch returns 0 | Issues were closed by reset but seeder was checking `state=all` | Fixed — seeder now checks `state=open` only |
| Fetch returns 0 right after seeding | GitHub label index delay (~3–5s) | Use `--seed-delay 5` |
| `ConfigError: GITHUB_TOKEN not set` | `.env` missing or incomplete | Ensure `.env` exists with all required keys |
| Labels not applied to issues | Labels not created in the repo before issue creation | `ensure_labels` runs before `create_issue` — check PAT has `Issues: write` scope |
