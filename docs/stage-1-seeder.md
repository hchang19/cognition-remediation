# Stage 1 — Issue Seeder

One-shot script. Creates the full issue set in the fork that Devin will remediate. **No internal dependencies** — uses `github_session` from `app/shared/` and raw `requests`. No SQLite writes — the orchestrator handles DB writes when the webhook fires.

Validate this against GitHub before building any other component.

## Files

```
scripts/seed_issues.py
scripts/reset_demo.py
config/issues.yml
```

---

## Issue Types

The seeder covers four types across three complexity tiers. `complexity` drives orchestrator routing; `type` is for dashboard segmentation and reporting.

| Type | Label | Description |
|---|---|---|
| Vulnerability | `type:vulnerability` | CVE with a known fix — specific file, version pin |
| Upgrade | `type:upgrade` | Outdated dependency, no CVE, version bump needed |
| Bug | `type:bug` | Reported defect with a known or suspected location |
| Feature request | `type:feature` | New behavior with defined acceptance criteria |
| Ambiguous | `type:ambiguous` | Vague ask — orchestrator declines, routes to human |

---

## `config/issues.yml`

Source of truth. The script reads this and creates issues idempotently. Edit this file to change the issue set, not the script.

```yaml
issues:

  # --- DEFINITE: CVE ---
  - title: "Upgrade urllib3 from 1.26.5 to 1.26.18 (CVE-2023-45803)"
    complexity: definite
    source: pip-audit
    severity: high
    type: vulnerability
    labels: [auto-remediate, complexity:definite, source:pip-audit, severity:high, type:vulnerability]
    body: |
      ## Summary
      urllib3 1.26.5 is affected by CVE-2023-45803. Fix by upgrading to 1.26.18.

      ## Source
      pip-audit / CVE-2023-45803

      ## Location
      - File: `requirements/base.txt`
      - Line: 87

      ## Details
      urllib3 1.26.5 does not strip the HTTP request body on cross-origin redirects,
      leading to potential information disclosure. Fixed in 1.26.18.

      ## Suggested Remediation
      Change line 87 from `urllib3==1.26.5` to `urllib3==1.26.18`. Verify no conflicts
      in `requirements/development.txt`.

      ## Acceptance Criteria
      - [ ] urllib3 pinned to 1.26.18 in requirements/base.txt
      - [ ] No conflicts in other requirements files
      - [ ] CI passes
      - [ ] No new pip-audit findings introduced

      <!-- cognition-meta
      {"complexity": "definite", "source": "pip-audit", "type": "vulnerability", "idempotency_key": "pip-audit-CVE-2023-45803-urllib3"}
      -->

  # --- DEFINITE: VERSION UPGRADE ---
  - title: "Upgrade Pillow to >=10.0.1 (outdated, upstream EOL)"
    complexity: definite
    source: manual
    severity: medium
    type: upgrade
    labels: [auto-remediate, complexity:definite, source:manual, severity:medium, type:upgrade]
    body: |
      ## Summary
      Pillow is pinned to 9.x in requirements/base.txt. Upstream dropped support for
      9.x in January 2024. Upgrade to >=10.0.1.

      ## Location
      - File: `requirements/base.txt`

      ## Suggested Remediation
      Bump Pillow pin to `Pillow>=10.0.1,<11`. Run `pip install -r requirements/base.txt`
      to verify resolution. Check Pillow 10.x changelog for breaking API changes before merging.

      ## Acceptance Criteria
      - [ ] Pillow pinned to >=10.0.1 in requirements/base.txt
      - [ ] No import errors in CI
      - [ ] Existing image-related tests pass

      <!-- cognition-meta
      {"complexity": "definite", "source": "manual", "type": "upgrade", "idempotency_key": "manual-upgrade-pillow-10"}
      -->

  # --- DEFINITE: BUG (specific location + reproducer) ---
  - title: "parse_human_datetime() raises ValueError on empty string input"
    complexity: definite
    source: manual
    severity: medium
    type: bug
    labels: [auto-remediate, complexity:definite, source:manual, severity:medium, type:bug]
    body: |
      ## Summary
      `superset/utils/core.py:parse_human_datetime()` raises `ValueError` instead of
      returning `None` when given an empty string, breaking the schedule editor UI.

      ## Location
      - File: `superset/utils/core.py`
      - Function: `parse_human_datetime`

      ## Reproducer
      ```python
      from superset.utils.core import parse_human_datetime
      parse_human_datetime("")  # raises ValueError, should return None
      ```

      ## Suggested Remediation
      Add a guard at the top of `parse_human_datetime` returning `None` if input is
      empty or whitespace-only. Add unit tests in `tests/unit_tests/utils/core_test.py`.

      ## Acceptance Criteria
      - [ ] `parse_human_datetime("")` returns None
      - [ ] `parse_human_datetime("   ")` returns None
      - [ ] `parse_human_datetime(None)` returns None
      - [ ] Existing behavior for valid inputs unchanged
      - [ ] Unit tests added for all three cases

      <!-- cognition-meta
      {"complexity": "definite", "source": "manual", "type": "bug", "idempotency_key": "manual-bug-parse-human-datetime-empty"}
      -->

  # --- SEMI-DEFINITE: BUG (user report, location unknown) ---
  - title: "Dashboard filters silently fail for Unicode characters in filter values"
    complexity: semi-definite
    source: manual
    severity: high
    type: bug
    labels: [auto-remediate, complexity:semi-definite, source:manual, severity:high, type:bug]
    body: |
      ## Summary
      Dashboard filters appear applied in the UI but the underlying chart data is
      unfiltered when values contain certain Unicode characters.

      ## Source
      Consolidated from user reports #28341, #28507, #28614

      ## Location
      Unknown — likely in filter serialization or query builder.
      Suspected candidates:
      - `superset-frontend/src/dashboard/components/nativeFilters/`
      - `superset/common/query_context.py`
      - `superset/connectors/sqla/models.py`

      ## Reproducer
      1. Create a dashboard with a text filter on a string column
      2. Apply filter value: `John's Data` (curly apostrophe)
      3. Chart updates but data is not filtered

      Affects: emoji (🚀), curly quotes (', "), em dashes (—)

      ## Acceptance Criteria
      - [ ] Root cause identified and documented in PR description
      - [ ] Filter correctly applied for Unicode values
      - [ ] Regression test added covering at least 3 character classes
      - [ ] User-visible error shown if filter cannot be applied

      <!-- cognition-meta
      {"complexity": "semi-definite", "source": "manual", "type": "bug", "idempotency_key": "manual-bug-unicode-filter"}
      -->

  # --- SEMI-DEFINITE: FEATURE REQUEST ---
  - title: "Add CSV export option to SQL Lab results panel"
    complexity: semi-definite
    source: manual
    severity: low
    type: feature
    labels: [auto-remediate, complexity:semi-definite, source:manual, severity:low, type:feature]
    body: |
      ## Summary
      SQL Lab has no direct CSV export button on the results panel. Users must copy
      results manually or re-run queries through a dashboard.

      ## Source
      Feature request — Customer Success, multiple accounts

      ## Location
      Likely: `superset-frontend/src/SqlLab/components/ResultSet/`

      ## Details
      Add a "Download CSV" button to the SQL Lab results panel that exports the
      current result set as a CSV file without requiring a page reload or re-query.

      ## Acceptance Criteria
      - [ ] "Download CSV" button appears in the results panel toolbar
      - [ ] Clicking it downloads the current result set as a .csv file
      - [ ] Works for result sets up to 10k rows
      - [ ] Button is disabled when no results are loaded
      - [ ] Unit test or Cypress test added

      <!-- cognition-meta
      {"complexity": "semi-definite", "source": "manual", "type": "feature", "idempotency_key": "manual-feature-csv-export-sql-lab"}
      -->

  # --- AMBIGUOUS: vague performance ask ---
  - title: "Make the dashboard rendering faster"
    complexity: ambiguous
    source: manual
    severity: low
    type: ambiguous
    labels: [auto-remediate, complexity:ambiguous, source:manual, severity:low, type:ambiguous]
    body: |
      ## Summary
      The dashboard feels slow. We should make it faster.

      ## Details
      The dashboard feels slow.

      ## Suggested Remediation
      N/A

      ## Acceptance Criteria
      - [ ] Dashboard is faster

      <!-- cognition-meta
      {"complexity": "ambiguous", "source": "manual", "type": "ambiguous", "idempotency_key": "manual-ambiguous-dashboard-faster"}
      -->
```

**Complexity distribution:** 3 definite, 2 semi-definite, 1 ambiguous. Adjust counts in `config/issues.yml` — the script is driven entirely by this file.

---

## Specificity Spectrum

The `complexity` label is the most important dimension for evaluating Devin and segmenting the dashboard.

| Complexity | What it means | Expected outcome |
|---|---|---|
| `definite` | Exact file, function, or CVE. Fix is unambiguous. | Devin succeeds, opens PR, CI passes |
| `semi-definite` | Intent clear, location partially known. Requires investigation. | Devin may succeed or need human handoff |
| `ambiguous` | Vague or contradictory. No actionable anchor. | Orchestrator declines — labels `needs-human-scoping`, logs `session.declined` |

The ambiguous decline is deliberate. The system demonstrates it knows its own limits.

---

## `scripts/seed_issues.py`

Uses `github_session` from `app/shared/github_session.py`. No PyGithub, no SQLite.

```
1. Load config/issues.yml
2. Ensure all required labels exist in the fork — create any missing via POST /repos/{repo}/labels
3. For each issue:
   a. Search open + closed issues for matching idempotency_key in body text
   b. If found → skip (print "skipped: {title}")
   c. If not → POST /repos/{repo}/issues with title, body, labels
4. Print summary: N created, M skipped
```

---

## `scripts/reset_demo.py`

```
1. GET all issues with label `auto-remediate`
2. PATCH each to state=closed
3. Connect to SQLite directly and DELETE FROM events; DELETE FROM sessions; DELETE FROM issues
4. Print confirmation
```

The only script that touches SQLite outside the normal app lifecycle.
