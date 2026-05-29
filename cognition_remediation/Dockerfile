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
