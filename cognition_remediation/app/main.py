"""FastAPI application entrypoint.

Exposes GET /healthz and conditionally POST /webhook (when
GITHUB_WEBHOOK_SECRET is configured). Starts the background poller thread
on startup.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager

import sqlite3
from fastapi import FastAPI

from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.poller import start_poller
from app.shared.config import Config, load_config
from app.shared.github_session import github_session
from app.shared.logger import get_logger
from app.db import get_db
from app.webhook import router as webhook_router

logger = get_logger(__name__, log_file="cognition.log")


def create_app(
    cfg: Config | None = None,
    db: sqlite3.Connection | None = None,
    devin: DevinClient | None = None,
    gh: GitHubClient | None = None,
    start_background: bool = False,
) -> FastAPI:
    """Factory used by tests (inject mocks) and production (load from env)."""

    resolved_cfg = cfg or load_config()
    resolved_db = db or get_db(resolved_cfg.db_path)
    resolved_devin = devin or DevinClient(api_key=resolved_cfg.devin_api_key, org_id=resolved_cfg.devin_org_id)
    resolved_gh = gh or GitHubClient(
        session=github_session(resolved_cfg.github_token),
        repo=resolved_cfg.github_repo,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cfg = resolved_cfg
        app.state.db = resolved_db
        app.state.devin = resolved_devin
        app.state.gh = resolved_gh
        if start_background:
            t = threading.Thread(
                target=start_poller,
                args=(resolved_db, resolved_devin, resolved_gh, resolved_cfg),
                daemon=True,
            )
            t.start()
        yield
        resolved_db.close()

    application = FastAPI(lifespan=lifespan)
    application.include_router(webhook_router)

    @application.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return application


# Production entrypoint — only runs when GITHUB_TOKEN env var is set so
# test imports of create_app don't fail due to missing env vars.
if os.environ.get("GITHUB_TOKEN"):
    from dotenv import load_dotenv
    load_dotenv()
    app = create_app(start_background=True)
