"""Thin wrapper around the GitHub REST API.

Uses ``github_session`` from ``app/shared/github_session.py`` — raw
``requests``, no PyGithub dependency. All methods use ``with_retry`` for
rate-limit and 5xx resilience.
"""

from __future__ import annotations

import requests
from dataclasses import dataclass, field

from app.shared.logger import get_logger
from app.shared.retry import with_retry

logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"


@dataclass
class Issue:
    number: int
    title: str
    labels: list[str]
    body: str


@dataclass
class Commit:
    sha: str
    author: str


@dataclass
class CIRun:
    run_id: int
    status: str            # "queued" | "in_progress" | "completed"
    conclusion: str | None  # "success" | "failure" | "cancelled" | None
    started_at: str
    completed_at: str | None


@dataclass
class PullRequest:
    number: int
    head_ref: str
    title: str


class GitHubClient:
    def __init__(self, session: requests.Session, repo: str) -> None:
        self._session = session
        self._repo = repo

    @with_retry()
    def _get(self, url: str, **kwargs) -> requests.Response:
        r = self._session.get(url, **kwargs)
        r.raise_for_status()
        return r

    @with_retry()
    def _post(self, url: str, **kwargs) -> requests.Response:
        r = self._session.post(url, **kwargs)
        r.raise_for_status()
        return r

    def get_open_issues(self, label: str) -> list[Issue]:
        """Return all open issues with the given label (excludes PRs)."""
        issues: list[Issue] = []
        params: dict = {"state": "open", "labels": label, "per_page": 100, "page": 1}
        while True:
            r = self._get(f"{_GITHUB_API}/repos/{self._repo}/issues", params=params)
            batch = r.json()
            if not batch:
                break
            for item in batch:
                if "pull_request" in item:
                    continue
                issues.append(Issue(
                    number=item["number"],
                    title=item["title"],
                    labels=[lb["name"] for lb in item.get("labels", [])],
                    body=item.get("body") or "",
                ))
            if len(batch) < params["per_page"]:
                break
            params["page"] += 1
        logger.info("github.issues_fetched", extra={"label": label, "count": len(issues)})
        return issues

    def get_pr_commits(self, pr_number: int) -> list[Commit]:
        """Return commits on a pull request."""
        r = self._get(f"{_GITHUB_API}/repos/{self._repo}/pulls/{pr_number}/commits")
        commits = [
            Commit(sha=c["sha"], author=c["commit"]["author"]["name"])
            for c in r.json()
        ]
        logger.info("github.commits_fetched", extra={"pr_number": pr_number, "count": len(commits)})
        return commits

    def get_latest_ci_run(self, pr_number: int) -> CIRun | None:
        """Return the most recent Actions run for the PR's head SHA, or None."""
        pr = self._get(f"{_GITHUB_API}/repos/{self._repo}/pulls/{pr_number}").json()
        head_sha = pr["head"]["sha"]
        r = self._get(
            f"{_GITHUB_API}/repos/{self._repo}/actions/runs",
            params={"head_sha": head_sha, "per_page": 1},
        )
        runs = r.json().get("workflow_runs", [])
        if not runs:
            logger.info("github.ci_run_fetched", extra={"pr_number": pr_number, "run": None})
            return None
        run = runs[0]
        result = CIRun(
            run_id=run["id"],
            status=run["status"],
            conclusion=run.get("conclusion"),
            started_at=run["run_started_at"],
            completed_at=run.get("updated_at") if run["status"] == "completed" else None,
        )
        logger.info(
            "github.ci_run_fetched",
            extra={"pr_number": pr_number, "status": result.status},
        )
        return result

    def add_label(self, issue_number: int, label: str) -> None:
        """Add a label to an issue."""
        self._post(
            f"{_GITHUB_API}/repos/{self._repo}/issues/{issue_number}/labels",
            json={"labels": [label]},
        )
        logger.info("github.label_added", extra={"issue_number": issue_number, "label": label})

    def post_comment(self, issue_number: int, body: str) -> None:
        """Post a comment on an issue or PR."""
        self._post(
            f"{_GITHUB_API}/repos/{self._repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        logger.info("github.comment_posted", extra={"issue_number": issue_number})

    @with_retry()
    def _patch(self, url: str, **kwargs) -> requests.Response:
        r = self._session.patch(url, **kwargs)
        r.raise_for_status()
        return r

    @with_retry()
    def _put(self, url: str, **kwargs) -> requests.Response:
        r = self._session.put(url, **kwargs)
        r.raise_for_status()
        return r

    @with_retry()
    def _delete(self, url: str, **kwargs) -> requests.Response:
        r = self._session.delete(url, **kwargs)
        r.raise_for_status()
        return r

    def get_open_prs(self) -> list[PullRequest]:
        """Return all open pull requests."""
        prs: list[PullRequest] = []
        params: dict = {"state": "open", "per_page": 100, "page": 1}
        while True:
            r = self._get(f"{_GITHUB_API}/repos/{self._repo}/pulls", params=params)
            batch = r.json()
            if not batch:
                break
            for item in batch:
                prs.append(PullRequest(
                    number=item["number"],
                    head_ref=item["head"]["ref"],
                    title=item["title"],
                ))
            if len(batch) < params["per_page"]:
                break
            params["page"] += 1
        logger.info("github.prs_fetched", extra={"count": len(prs)})
        return prs

    def close_issue(self, issue_number: int) -> None:
        """Close a GitHub issue."""
        self._patch(
            f"{_GITHUB_API}/repos/{self._repo}/issues/{issue_number}",
            json={"state": "closed"},
        )
        logger.info("github.issue_closed", extra={"issue_number": issue_number})

    def close_pr(self, pr_number: int) -> None:
        """Close (without merging) a pull request."""
        self._patch(
            f"{_GITHUB_API}/repos/{self._repo}/pulls/{pr_number}",
            json={"state": "closed"},
        )
        logger.info("github.pr_closed", extra={"pr_number": pr_number})

    def delete_branch(self, branch: str) -> None:
        """Delete a branch by ref name."""
        self._delete(f"{_GITHUB_API}/repos/{self._repo}/git/refs/heads/{branch}")
        logger.info("github.branch_deleted", extra={"branch": branch})

    def remove_label(self, issue_number: int, label: str) -> None:
        """Remove a single label from an issue. No-ops if the label is not present (404)."""
        try:
            self._delete(f"{_GITHUB_API}/repos/{self._repo}/issues/{issue_number}/labels/{label}")
            logger.info("github.label_removed", extra={"issue_number": issue_number, "label": label})
        except Exception as exc:
            # 404 = label wasn't on the issue — safe to ignore
            logger.debug("github.label_remove_skipped", extra={"issue_number": issue_number, "label": label, "error": str(exc)})

    def merge_pr(self, pr_number: int, merge_method: str = "squash") -> None:
        """Merge a pull request. Raises requests.HTTPError on 405 (not mergeable) or 409 (conflict)."""
        self._put(
            f"{_GITHUB_API}/repos/{self._repo}/pulls/{pr_number}/merge",
            json={"merge_method": merge_method},
        )
        logger.info("github.pr_merged", extra={"pr_number": pr_number, "merge_method": merge_method})
