"""Devin prompt templates per complexity tier.

Rendered with issue fields before sending to Devin. Keep prompts surgical —
Devin performs best with explicit acceptance criteria and clear boundaries.
"""

from __future__ import annotations

import re
from app.github_client import Issue


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]


_CONTRIBUTION_GUIDE_INSTRUCTION = (
    "- Before writing any code, read CONTRIBUTING.md in the repository root "
    "and follow its conventions for commits, testing, and PR style"
)


def definite_prompt(issue: Issue) -> str:
    return f"""You are remediating a security vulnerability in apache/superset.

Issue #{issue.number}: {issue.title}

{issue.body}

Instructions:
- Branch name: fix/{issue.number}-{_slug(issue.title)}
{_CONTRIBUTION_GUIDE_INSTRUCTION}
- Change only what is required to resolve the issue
- Do not refactor surrounding code
- Run existing tests without modifying them
- Open a PR that closes issue #{issue.number} — include "Closes #{issue.number}" in the PR description so GitHub auto-closes the issue on merge
"""


def semi_definite_prompt(issue: Issue) -> str:
    return f"""You are investigating and remediating a reported issue in apache/superset.

Issue #{issue.number}: {issue.title}

{issue.body}

Instructions:
- Read the full issue before touching code
{_CONTRIBUTION_GUIDE_INSTRUCTION}
- Document root cause in the PR description before implementing
- If a fix requires a design decision, open a follow-up issue instead of choosing unilaterally
- Branch name: fix/{issue.number}-{_slug(issue.title)}
- Open a PR that closes issue #{issue.number} — include "Closes #{issue.number}" in the PR description; include root cause, what changed, open questions
"""
