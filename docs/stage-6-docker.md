# Stage 6 — Docker Compose

Single-command startup for both services with a shared SQLite volume.

**Depends on:** Stage 4 (orchestrator FastAPI app) and Stage 5 (Streamlit dashboard) must exist first.

---

## Goal

`docker compose up --build` from `cognition_remediation/` starts both services against a shared SQLite file. No manual environment sourcing, no port conflicts, no host Python dependency.

---

## Files to Create

Both files live inside `cognition_remediation/`.

```
cognition_remediation/
├── Dockerfile
└── docker-compose.yml
```

---

## `docker-compose.yml`

Two services, one named volume.

```yaml
version: "3.9"

services:
  orchestrator:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    volumes:
      - db-data:/data
    env_file: .env
    environment:
      DB_PATH: /data/cognition.db

  dashboard:
    build: .
    command: streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0
    ports:
      - "8501:8501"
    volumes:
      - db-data:/data
    env_file: .env
    environment:
      DB_PATH: /data/cognition.db

volumes:
  db-data:
```

Key decisions:
- `db-data` is a named volume — survives `docker compose down`, wiped only by `docker compose down -v`
- Both services mount `/data` — `DB_PATH=/data/cognition.db` is injected via `environment`, overriding any value in `.env`
- `env_file: .env` supplies all API keys; `.env` must exist before `docker compose up`
- Each service overrides `CMD` in compose rather than hard-coding it in the Dockerfile — one image, two roles

---

## `Dockerfile`

Multi-stage build. Base stage installs deps; final stage copies application code.

```dockerfile
# ── base: install Python dependencies ─────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── final ─────────────────────────────────────────────────────────────────────
FROM base AS final

COPY . .

# Both ports declared; the active one depends on CMD override in docker-compose.yml
EXPOSE 8000 8501
```

Notes:
- `python:3.11-slim` keeps the image small; no build tools needed for pure-Python deps
- `COPY . .` copies the full `cognition_remediation/` tree — both `app/` and `dashboard/` land in `/app`
- No `CMD` here; each compose service supplies its own command

---

## Run Instructions

```bash
cd cognition_remediation
cp ../.env.example .env          # fill in credentials if not already done
docker compose up --build        # build image and start both services
```

One-off scripts against the running container:

```bash
docker compose run orchestrator python3 -m scripts.seed_issues    # seed issues
docker compose run orchestrator python3 -m scripts.reset_demo     # wipe DB + close issues
```

Stop and preserve the database:

```bash
docker compose down              # containers stop, db-data volume kept
```

Wipe everything including the database:

```bash
docker compose down -v           # removes the db-data volume
```

---

## Future State

- Split into a dedicated base image pushed to a registry so CI can pull instead of rebuild
- Add a `healthcheck` directive so compose waits for the orchestrator before starting the dashboard
- Switch `db-data` to a bind mount to a developer-controlled path for easier local inspection
- In production: replace SQLite + named volume with a Postgres service block in compose
