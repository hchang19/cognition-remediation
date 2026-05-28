# Testing

Tests live in `cognition_remediation/tests/` and use two pytest markers to separate offline and network-dependent runs.

## Markers

| Marker | What it tests | Network required |
|---|---|---|
| `unit` | Fully mocked, offline-safe | No |
| `integration` | Real GitHub API or real filesystem | Yes (`.env`) |

## Setup

```bash
cd cognition_remediation
pip install -r requirements.txt
pip install pytest pytest-mock
```

Integration tests load `.env` automatically via `python-dotenv`. Ensure at minimum:

```
GITHUB_TOKEN=<your fine-grained PAT>
GITHUB_REPO=hchang19/superset
```

## Running Tests

```bash
# Fast offline suite — no credentials needed
pytest -m unit -v

# Integration suite — requires .env
pytest -m integration -v

# Full suite
pytest -v

# Single file
pytest tests/test_retry.py -v
pytest tests/test_db.py -v
pytest tests/test_seeder.py -v
```

## Coverage by Module

| Source file | Test file | Unit | Integration |
|---|---|---|---|
| `app/shared/retry.py` | `tests/test_retry.py` | 8 cases | 1 case |
| `app/db.py` + `app/events.py` | `tests/test_db.py` | ~18 cases | 1 case |
| `scripts/seed_issues.py` | `tests/test_seeder.py` | 9 cases | 1 case |

## Adding Tests

- Place new test files in `cognition_remediation/tests/`.
- Mark every test with `@pytest.mark.unit` or `@pytest.mark.integration`.
- Integration tests must call `pytest.skip(...)` when required env vars are absent.
- Shared fixtures go in `tests/conftest.py`.
