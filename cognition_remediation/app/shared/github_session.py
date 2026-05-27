"""Pre-authenticated requests.Session for GitHub REST API.

Used by the seeder (stage 1) and GitHub client (stage 3) to avoid
duplicating auth header setup.
"""

from __future__ import annotations

import requests

GITHUB_API_VERSION = "2022-11-28"


def github_session(token: str) -> requests.Session:
    """Return a session with Authorization, Accept, and X-GitHub-Api-Version headers set."""
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "cognition-remediation/0.1",
        }
    )
    return session
