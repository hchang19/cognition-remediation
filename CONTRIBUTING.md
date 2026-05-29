# Contributing

## Branching

All work happens on feature branches — never commit directly to `main`.

```bash
git checkout -b feature/<short-description>
```

Merge only via PR after review. Branch naming examples:
- `feature/stage-3-clients`
- `fix/seed-log-key`
- `docs/stage-6-docker`

## Development setup

```bash
cd cognition_remediation
pip install -r requirements.txt
pip install pytest pytest-mock
cp .env.example .env   # fill in credentials
```

## Running tests

```bash
# Offline unit tests (no credentials needed)
pytest -m unit -v

# Integration tests (requires .env)
pytest -m integration -v

# Full suite
pytest -v
```

See `cognition_remediation/docs/testing.md` for full coverage details.

## Running the demo

```bash
cd cognition_remediation
python3 -m scripts.demo
```

See `cognition_remediation/docs/demo.md` for the full runbook.

## Commit style

Use the conventional commits prefix:

| Prefix | When |
|---|---|
| `feat:` | New feature or capability |
| `fix:` | Bug fix |
| `test:` | Adding or fixing tests |
| `docs:` | Documentation only |
| `refactor:` | Code change with no behavior change |

Example: `feat: add GitHubClient.post_comment`

## Coding conventions

- No comments unless the WHY is non-obvious
- No `print()` — use `get_logger(__name__)` with structured `extra={}` fields
- New scripts must call `load_dotenv()` before reading env vars
- All tests marked `@pytest.mark.unit` or `@pytest.mark.integration`
- Integration tests must `pytest.skip()` when credentials are absent
