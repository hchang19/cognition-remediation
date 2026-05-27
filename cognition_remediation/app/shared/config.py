"""Centralized env var loading and validation. Raises on startup if required vars are missing."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    github_token: str
    github_repo: str  # "owner/repo"
    github_webhook_secret: str | None
    devin_api_key: str
    devin_daily_limit: int
    pause: bool
    db_path: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


def load_config() -> Config:
    return Config(
        github_token=_require("GITHUB_TOKEN"),
        github_repo=_require("GITHUB_REPO"),
        github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET") or None,
        devin_api_key=_require("DEVIN_API_KEY"),
        devin_daily_limit=int(os.environ.get("DEVIN_DAILY_SESSION_LIMIT", "10")),
        pause=bool(os.environ.get("PAUSE")),
        db_path=os.environ.get("DB_PATH", "cognition.db"),
    )
