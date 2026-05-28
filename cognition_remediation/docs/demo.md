# Demo Runbook

End-to-end walkthrough of the seeder and GitHub verification steps.

## Prerequisites

`.env` configured with valid credentials (see `.env.example`):

```
GITHUB_TOKEN=<fine-grained PAT with Issues: read/write>
GITHUB_REPO=hchang19/superset
DEVIN_API_KEY=<your key>
```

## Run the full demo

```bash
cd cognition_remediation
set -a && source .env && set +a
python3 -m scripts.demo
```

Expected output:

```
=== Step 1: Seeding issues ===
  SKIP  Upgrade urllib3 from 1.26.5 to 1.26.18 (CVE-2023-45803)
  SKIP  Upgrade Pillow to >=10.0.1 (outdated, upstream EOL)
  ...
  Done — created: 0, skipped: 6

=== Step 2: Fetching open issues from GitHub ===
  Found 6 open issue(s) in hchang19/superset

#     Title                                                        Labels
----------------------------------------------------------------------------------------------------
#1    Upgrade urllib3 from 1.26.5 to 1.26.18 (CVE-2023-45803)    auto-remediate, complexity:definite, ...
#2    Upgrade Pillow to >=10.0.1 (outdated, upstream EOL)         auto-remediate, complexity:definite, ...
...
```

On first run, `created` will be 6. Subsequent runs skip all (idempotent).

## Run steps individually

**Seed only:**
```bash
python3 -m scripts.seed_issues
```

**Reset and re-seed (wipes issues + DB):**
```bash
python3 -m scripts.reset_demo
python3 -m scripts.demo
```

## Known issues

| Issue | Cause | Fix |
|---|---|---|
| Labels show as `(none)` on GitHub | Fine-grained PAT missing label-assignment scope | Regenerate token with **Issues: Read and write** at `github.com/settings/tokens` |
| `ConfigError: GITHUB_TOKEN not set` | `.env` not sourced | Run `set -a && source .env && set +a` before the script |
