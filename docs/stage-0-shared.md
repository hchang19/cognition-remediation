# Stage 0 — Shared Utilities

Build before any other stage. These helpers are imported across all stages.

## Files

```
app/shared/
    github_session.py   # authenticated GitHub HTTP session
    config.py           # env var loading + validation
    retry.py            # exponential backoff decorator
    logger.py           # structured logging setup
```

---

## `app/shared/config.py`

Single place to load and validate all env vars. Raises on startup if required vars are missing.

```python
@dataclass
class Config:
    github_token: str
    github_repo: str            # "owner/repo"
    github_webhook_secret: str | None
    devin_api_key: str
    devin_daily_limit: int      # default: 10
    pause: bool                 # True if PAUSE env var is set
    db_path: str                # default: "cognition.db"

def load_config() -> Config: ...
```

---

## `app/shared/github_session.py`

Pre-configured `requests.Session` for GitHub REST API. Used by the seeder (stage 1) and GitHub client (stage 3) — avoids duplicating auth header setup.

```python
def github_session(token: str) -> requests.Session:
    """Returns a session with Authorization, Accept, and X-GitHub-Api-Version headers set."""
```

Usage:
```python
from app.shared.github_session import github_session
session = github_session(config.github_token)
session.post(f"https://api.github.com/repos/{repo}/issues", json={...})
```

---

## `app/shared/retry.py`

Exponential backoff decorator. Used by Devin client and GitHub client.

```python
def with_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """Decorator. Retries on requests.HTTPError (5xx) and ConnectionError.
    Delays: 1s, 2s, 4s. Raises original exception after exhaustion."""
```

---

## `app/shared/logger.py`

Structured JSON logger. One import, consistent format across all stages.

```python
def get_logger(name: str) -> logging.Logger:
    """Returns a logger writing structured JSON lines to stdout."""
```

Log fields: `timestamp`, `level`, `name`, `message`, plus any `extra` kwargs passed at call site.
