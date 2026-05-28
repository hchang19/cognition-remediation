# Apache Superset Contribution Guide

## 1. Environment Setup

```bash
# Verify Superset is running
curl -f http://localhost:8088/health && echo "ready"

# Install Python dev tools
pip install -r requirements/development.txt

# Install pre-commit hooks (one-time)
pre-commit install
```

---

## 2. Pre-commit — Run Before Every Push

**Non-negotiable: always run pre-commit before pushing. CI will fail otherwise.**

```bash
git add .                      # stage changes first — pre-commit only checks staged files
pre-commit run                 # staged files only (fast)
pre-commit run --all-files     # full check, same as CI
```

### What each hook does

| Hook | Type | What it checks |
|---|---|---|
| `ruff-format` | auto-fix | Python formatting |
| `ruff` | auto-fix | Python linting |
| `mypy` | manual fix | Python type checking |
| `pylint` | manual fix | Python linting (custom Superset rules) |
| `prettier-frontend` | auto-fix | JS/TS/CSS/JSON formatting |
| `oxlint-frontend` | auto-fix | TypeScript/JS linting |
| `type-checking-frontend` | manual fix | TypeScript type checking |
| `auto-walrus` | auto-fix | Python walrus operator modernization |
| `trailing-whitespace` | auto-fix | Trailing whitespace |
| `end-of-file-fixer` | auto-fix | Missing newlines at EOF |
| `check-yaml` | fail | Invalid YAML |
| `debug-statements` | fail | Leftover `pdb`/`breakpoint()` calls |
| `check-added-large-files` | fail | Large accidental file additions |
| `db-engine-spec-metadata` | fail | DB engine spec validation |
| `feature-flags-sync` | fail | Feature flag doc out of sync |

After auto-fix hooks run, re-stage and recommit:

```bash
git add .
git commit --amend   # or new commit
```

Run a single hook:

```bash
pre-commit run mypy
pre-commit run prettier
pre-commit run eslint
```

---

## 3. Python Standards

- **Type hints required** on all new functions and classes
- **Docstrings required** on all new functions and classes
- **MyPy compliant** — run `pre-commit run mypy`
- Use `ruff` for linting, `ruff format` for formatting (PEP 8)
- Use `~Model.field` instead of `Model.field == False` (avoids ruff E712)
- Access `app.config["KEY"]` directly, not `.get("KEY")` — avoids `Optional` typing issues
- New models should use **UUID primary keys**, not auto-incrementing integers
- For existing models, add UUID fields alongside integer IDs for gradual migration

---

## 4. TypeScript/Frontend Standards

- **No `any` types** — use proper TypeScript types
- **No `.js` files** — write `.ts`/`.tsx` only
- **Functional components** with hooks (no class components)
- **No direct Ant Design imports** — use `@superset-ui/core/components` wrappers
- Use antd theming tokens, not legacy custom CSS or style props
- Use `Jest + React Testing Library` for component tests (no Enzyme)
- Use `test()` at the top level, not nested `describe()` blocks

---

## 5. Test Commands

### Backend (Python)

```bash
pytest tests/unit_tests/                          # all unit tests
pytest tests/unit_tests/specific_test.py          # single file
pytest tests/integration_tests/                   # integration tests
pre-commit run mypy                               # type checking only
```

### Frontend (TypeScript)

```bash
cd superset-frontend
npm run test                                      # all tests
npm run test -- path/to/file.test.tsx            # single file
npm run lint                                      # linting + type check
```

### E2E Tests

```bash
# Playwright (current)
npm run playwright:test                           # all tests
npm run playwright:ui                             # interactive mode
npm run playwright:headed                         # see browser
npx playwright test tests/auth/login.spec.ts      # single file

# Cypress — DEPRECATED, do not write new tests here
```

---

## 6. PR Title Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`, `other`

**Breaking change:** add `!` after type: `feat!: added foo to bar`

**Examples:**
```
feat(sqllab): add query cost estimation
fix(dashboard): resolve filter cascading issue
refactor(explore): simplify chart controls logic
perf(api): improve API info performance
```

---

## 7. PR Requirements Checklist

- [ ] `pre-commit run --all-files` passes
- [ ] Tests added/updated and passing
- [ ] No decrease in code coverage
- [ ] For UI changes: before/after screenshots or GIF (required — PR will be blocked without it)
- [ ] `docs/` updated if user-facing change
- [ ] `UPDATING.md` updated if breaking change
- [ ] PR template sections filled: SUMMARY, BEFORE/AFTER SCREENSHOTS, TESTING INSTRUCTIONS, ADDITIONAL INFORMATION
- [ ] DB migration required? Check the box in the template
- [ ] Feature flags required? Check the box in the template

For large features or public API changes, file a **Superset Improvement Proposal (SIP)** issue before writing code.

---

## 8. Apache License Headers

All new source files must include the ASF license header at the top:

```python
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# ...
```

Exception: LLM instruction files (`CLAUDE.md`, `AGENTS.md`, etc.) are excluded via `.rat-excludes`.

---

## 9. Backend API Patterns

```
superset/views/api.py        # REST endpoints with OpenAPI docstrings
superset/schemas.py          # Marshmallow validation schemas
superset/commands/           # Business logic with @transaction() decorators
superset/models/             # SQLAlchemy models
```

OpenAPI docs auto-generated at `/swagger/v1` from docstrings + schemas.

---

## 10. DB Migrations

```bash
# Generate migration
superset db migrate -m 'describe_your_change'

# Apply
superset db upgrade

# Rollback (always test this)
superset db downgrade

# If two migrations collide
superset db heads
superset db merge {HASH1} {HASH2}
```

Migration files go in `superset/migrations/versions/`, named `YYYY-MM-DD_HH-MM_hash_description.py`.

---

## 11. Code Comments

- **Do not use time-specific language** in comments (`now`, `currently`, `today`) — it rots
- Comments should be timeless and remain accurate regardless of when read

---

## 12. UI Text Capitalization

Use **sentence case** for all UI text:
- Correct: `"Select a database"`, `"Create new chart"`
- Wrong: `"Select a Database"`, `"Create New Chart"`

Exceptions: product names (Apache Superset), acronyms (SQL, API, CSV), proper nouns.
