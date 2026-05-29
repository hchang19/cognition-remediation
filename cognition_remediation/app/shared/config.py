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
    devin_org_id: str
    devin_daily_limit: int
    pause: bool
    db_path: str
    devin_session_cost_limit_usd: float | None    # terminate if cost exceeds this; None = no limit
    devin_session_time_limit_minutes: int | None  # terminate if session age exceeds this; None = no limit


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


def _optional_float(name: str) -> float | None:
    value = os.environ.get(name)
    return float(value) if value else None


def _optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else None


def load_config() -> Config:
    return Config(
        github_token=_require("GITHUB_TOKEN"),
        github_repo=_require("GITHUB_REPO"),
        github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET") or None,
        devin_api_key=_require("DEVIN_API_KEY"),
        devin_org_id=_require("DEVIN_ORG_ID"),
        devin_daily_limit=int(os.environ.get("DEVIN_DAILY_SESSION_LIMIT", "10")),
        pause=bool(os.environ.get("PAUSE")),
        db_path=os.environ.get("DB_PATH", "cognition.db"),
        devin_session_cost_limit_usd=_optional_float("DEVIN_SESSION_COST_LIMIT_USD"),
        devin_session_time_limit_minutes=_optional_int("DEVIN_SESSION_TIME_LIMIT_MINUTES"),
    )
