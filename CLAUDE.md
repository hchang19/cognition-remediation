# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Event-driven vulnerability remediation system for a Cognition take-home. See [README.md](./README.md) for run instructions and [DESIGN.md](./DESIGN.md) for full architecture.

**Status:** Design phase — no code yet. Code goes in `cognition_remediation/`.

## Coding Stages

Build in order. Each stage spec is self-contained — a coding agent can implement it without reading the others.

| Stage | Spec | Depends on |
|---|---|---|
| 0 | [docs/stage-0-shared.md](./docs/stage-0-shared.md) | — |
| 1 | [docs/stage-1-seeder.md](./docs/stage-1-seeder.md) | 0 |
| 2 | [docs/stage-2-db.md](./docs/stage-2-db.md) | 0 |
| 3 | [docs/stage-3-clients.md](./docs/stage-3-clients.md) | 0 |
| 4 | [docs/stage-4-orchestrator.md](./docs/stage-4-orchestrator.md) | 2, 3 |
| 5 | [docs/stage-5-dashboard.md](./docs/stage-5-dashboard.md) | 2 |

## Folder Structure

Mirrors [DESIGN.md § File Layout](./DESIGN.md#file-layout) exactly — DESIGN.md is the canonical reference.

```
cognition_remediation/
├── app/                  # orchestrator service (FastAPI, port 8000)
│   └── shared/           # config, github_session, retry, logger
├── dashboard/            # dashboard service (Streamlit, port 8501)
├── scripts/              # seed_issues.py, reset_demo.py
└── config/               # issues.yml
```

## Document Conventions

- **Architecture diagrams** reflect demo state only — no future state in diagrams
- **Future state** always goes in a dedicated section at the bottom of each file
- **Inline `**Future:**` callouts** are fine for single-line notes inside component descriptions
- **README.md** is the entry point — high-level only, links out for detail
- **DESIGN.md** is the source of truth for architecture and schema
- Audience is technical — no hand-holding

## Labels

All labels must exist in the fork before seeding. The orchestrator and dashboard filter on these.

| Label | Values | Purpose |
|---|---|---|
| `auto-remediate` | — | Required — orchestrator ignores issues without this |
| `complexity:*` | `definite` / `semi-definite` / `ambiguous` | Drives orchestrator routing and dashboard segmentation |
| `type:*` | `vulnerability` / `upgrade` / `bug` / `feature` / `ambiguous` | Issue category — used for dashboard breakdown |
| `source:*` | `pip-audit` / `manual` | Issue origin |
| `severity:*` | `critical` / `high` / `medium` / `low` | `critical` skips orchestrator, requires human triage |

## Demo vs. Production Trade-offs

| Decision | Demo | Production |
|---|---|---|
| Storage | SQLite, single file | Postgres + TimescaleDB |
| Event ingestion | Webhook + polling fallback | Kafka/Kinesis |
| Concurrency | One background thread | Separate worker processes |
| Metrics | SQL at read time | Stream-computed (Flink) |
| Session tracking | Poll Devin API every 30s | Devin webhooks |
| Issue discovery | Pre-seeded CVEs + manual issues | Full scanner suite on a cron |
| Dashboard | Streamlit local | Hosted React + real-time updates |
