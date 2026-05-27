# Stage 3 — Devin + GitHub Clients

Thin API wrappers. No business logic — that lives in the orchestrator.

## Files

```
app/devin_client.py
app/github_client.py
```

---

## Devin Client (`devin_client.py`)

Wraps the Devin REST API. Reference: https://docs.devin.ai/api-reference/overview

### Interface

```python
def create_session(prompt: str, repo_url: str, issue_id: int) -> str:
    """Creates a Devin session. Returns session_id."""

def get_session(session_id: str) -> SessionResponse:
    """Fetches current session state."""
```

### `SessionResponse` dataclass

```python
@dataclass
class SessionResponse:
    session_id: str
    status: str          # running / completed / failed / blocked
    cost_usd: float | None
    session_url: str | None
    pr_url: str | None
    output: str | None   # Devin's structured summary
```

### Retry behavior

Exponential backoff on 5xx / network errors: 3 attempts, delays 1s → 2s → 4s. Raises `DevinAPIError` on exhaustion — caller logs `session.start_failed` event.

---

## GitHub Client (`github_client.py`)

Uses `github_session` from `app/shared/github_session.py` — raw `requests`, no PyGithub dependency. Authenticated via `GITHUB_TOKEN`.

### Interface

```python
def get_open_issues(label: str) -> list[Issue]:
    """Returns open issues with the given label."""

def get_pr_commits(pr_number: int) -> list[Commit]:
    """Returns commits on a PR."""

def get_latest_ci_run(pr_number: int) -> CIRun | None:
    """Returns the most recent Actions run for the PR's head SHA."""

def add_label(issue_number: int, label: str) -> None:
    """Adds a label to an issue."""

def post_comment(issue_number: int, body: str) -> None:
    """Posts a comment on an issue."""
```

### `CIRun` dataclass

```python
@dataclass
class CIRun:
    run_id: int
    status: str       # queued / in_progress / completed
    conclusion: str | None  # success / failure / cancelled
    started_at: str
    completed_at: str | None
```

### Rate limiting

GitHub allows 5000 requests/hr on a PAT — well within demo scale. Sleep 60s and retry on 403/429.
