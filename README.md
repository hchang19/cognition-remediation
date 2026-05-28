# Cognition Interview — Vulnerability Remediation System

Event-driven automation that seeds vulnerability issues into a fork of `apache/superset`, dispatches Devin sessions to remediate them, and surfaces outcomes in a Streamlit dashboard.

Two Docker services share one SQLite database:
- **orchestrator** (port 8000) — FastAPI, webhook receiver, Devin dispatch, background poller
- **dashboard** (port 8501) — Streamlit, read-only view over SQLite

---

## Documentation

| File | Purpose |
|---|---|
| [DESIGN.md](./DESIGN.md) | Architecture, component design, schema, trade-offs |
| [DETAILS.md](./DETAILS.md) | KPI definitions — demo vs. future |
| [CLAUDE.md](./CLAUDE.md) | Coding conventions, stage map, document rules |

### Build Stages (in order)

| Stage | Spec | Depends on |
|---|---|---|
| 0 | [docs/stage-0-shared.md](./docs/stage-0-shared.md) | — |
| 1 | [docs/stage-1-seeder.md](./docs/stage-1-seeder.md) | 0 |
| 2 | [docs/stage-2-db.md](./docs/stage-2-db.md) | 0 |
| 3 | [docs/stage-3-clients.md](./docs/stage-3-clients.md) | 0 |
| 4 | [docs/stage-4-orchestrator.md](./docs/stage-4-orchestrator.md) | 2, 3 |
| 5 | [docs/stage-5-dashboard.md](./docs/stage-5-dashboard.md) | 2 |
| 6 | [docs/stage-6-docker.md](./docs/stage-6-docker.md) | 4, 5 |

Stages 2, 3 can run in parallel after 0. Stages 4, 5 can run in parallel after their deps.

---

## Running the Demo

```bash
# 1. Install dependencies (Python 3.10+ required)
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env           # fill in GITHUB_TOKEN, GITHUB_REPO, DEVIN_API_KEY

# 3. Run the demo script (seeds issues, then lists them from GitHub)
cd cognition_remediation && python3 -m scripts.demo

# 4. Run unit tests
pytest -m unit -v

# 5. Docker — single command startup (Stage 6, coming soon)
docker compose up --build
```

See [cognition_remediation/docs/demo.md](./cognition_remediation/docs/demo.md) for the full runbook, individual script commands, and reset instructions.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | yes | Fine-grained PAT: `issues:write`, `pull_requests:write`, `contents:read` |
| `GITHUB_REPO` | yes | `owner/repo` of the superset fork |
| `GITHUB_WEBHOOK_SECRET` | no | If unset, falls back to polling every 60s |
| `DEVIN_API_KEY` | yes | Devin API key |
| `DEVIN_DAILY_SESSION_LIMIT` | no | Hard session cap (default: 10) |
| `PAUSE` | no | Set to any value to halt new session creation |
