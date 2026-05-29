"""End-to-end tests for the Stage 3 client lifecycle.

Exercises complete workflows through DevinClient and GitHubClient using
mocked HTTP responses. Each test simulates a realistic multi-step
interaction that the Stage 4 orchestrator would perform.
"""

import pytest
import requests
from unittest.mock import MagicMock, patch, call

from app.devin_client import DevinClient, DevinAPIError, SessionResponse
from app.github_client import GitHubClient, Issue, Commit, CIRun
from app.shared.github_session import github_session


def _mock_response(status_code: int, body: dict | list) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body
    r.raise_for_status.return_value = None
    r.headers = {}
    return r


# ---------------------------------------------------------------------------
# DevinClient lifecycle E2E
# ---------------------------------------------------------------------------


class TestDevinClientLifecycle:
    """Simulates a complete Devin session lifecycle:
    create → poll (running) → poll (completed) → terminate.
    """

    def _make_client(self) -> DevinClient:
        return DevinClient(api_key="test-key", org_id="org-test")

    @pytest.mark.unit
    def test_full_session_lifecycle(self):
        client = self._make_client()

        post_responses = [
            _mock_response(200, {"session_id": "sess-e2e-1"}),
            _mock_response(200, {}),
        ]
        get_responses = [
            _mock_response(200, {
                "session_id": "sess-e2e-1",
                "status": "running",
                "cost_usd": 0.05,
                "session_url": "https://app.devin.ai/sessions/sess-e2e-1",
                "pr_url": None,
                "output": None,
            }),
            _mock_response(200, {
                "session_id": "sess-e2e-1",
                "status": "completed",
                "cost_usd": 1.50,
                "session_url": "https://app.devin.ai/sessions/sess-e2e-1",
                "pr_url": "https://github.com/org/repo/pull/42",
                "output": "Fixed CVE-2024-1234 by upgrading package X.",
            }),
        ]
        client._session.post = MagicMock(side_effect=post_responses)
        client._session.get = MagicMock(side_effect=get_responses)
        client._session.delete = MagicMock(return_value=_mock_response(200, {}))

        session_id = client.create_session(
            prompt="Fix CVE-2024-1234",
            repo_url="https://github.com/org/repo",
            issue_id=10,
        )
        assert session_id == "sess-e2e-1"

        resp1 = client.get_session(session_id)
        assert resp1.status == "running"
        assert resp1.pr_url is None

        resp2 = client.get_session(session_id)
        assert resp2.status == "completed"
        assert resp2.cost_usd == 1.50
        assert resp2.pr_url == "https://github.com/org/repo/pull/42"
        assert resp2.output == "Fixed CVE-2024-1234 by upgrading package X."

        client.terminate_session(session_id)

        assert client._session.post.call_count == 1  # create only
        assert client._session.get.call_count == 2
        assert client._session.delete.call_count == 1

    @pytest.mark.unit
    def test_session_fails_and_is_retried(self):
        """Simulates create → poll (running) → poll (failed) → create new → poll (completed)."""
        client = self._make_client()

        post_responses = [
            _mock_response(200, {"session_id": "sess-fail"}),
            _mock_response(200, {"session_id": "sess-retry"}),
        ]
        get_responses = [
            _mock_response(200, {
                "session_id": "sess-fail",
                "status": "running",
                "cost_usd": 0.10,
                "session_url": None,
                "pr_url": None,
                "output": None,
            }),
            _mock_response(200, {
                "session_id": "sess-fail",
                "status": "failed",
                "cost_usd": 0.25,
                "session_url": None,
                "pr_url": None,
                "output": "Session encountered an error.",
            }),
            _mock_response(200, {
                "session_id": "sess-retry",
                "status": "completed",
                "cost_usd": 1.00,
                "session_url": "https://app.devin.ai/sessions/sess-retry",
                "pr_url": "https://github.com/org/repo/pull/43",
                "output": "Fixed on retry.",
            }),
        ]
        client._session.post = MagicMock(side_effect=post_responses)
        client._session.get = MagicMock(side_effect=get_responses)

        sid1 = client.create_session("Fix bug", "https://github.com/org/repo", issue_id=5)
        assert sid1 == "sess-fail"

        resp = client.get_session(sid1)
        assert resp.status == "running"

        resp = client.get_session(sid1)
        assert resp.status == "failed"

        sid2 = client.create_session("Fix bug (retry)", "https://github.com/org/repo", issue_id=5)
        assert sid2 == "sess-retry"

        resp = client.get_session(sid2)
        assert resp.status == "completed"
        assert resp.pr_url == "https://github.com/org/repo/pull/43"

    @pytest.mark.unit
    def test_blocked_session_lifecycle(self):
        """Simulates create → poll (blocked) — orchestrator would notify human."""
        client = self._make_client()

        client._session.post = MagicMock(
            return_value=_mock_response(200, {"session_id": "sess-blocked"})
        )
        client._session.get = MagicMock(
            return_value=_mock_response(200, {
                "session_id": "sess-blocked",
                "status": "blocked",
                "cost_usd": 0.50,
                "session_url": "https://app.devin.ai/sessions/sess-blocked",
                "pr_url": None,
                "output": "Waiting for human input on auth credentials.",
            })
        )

        session_id = client.create_session("Fix auth", "https://github.com/org/repo", issue_id=20)
        resp = client.get_session(session_id)

        assert resp.status == "blocked"
        assert resp.output == "Waiting for human input on auth credentials."


# ---------------------------------------------------------------------------
# GitHubClient lifecycle E2E
# ---------------------------------------------------------------------------


class TestGitHubClientLifecycle:
    """Simulates a complete GitHub issue remediation workflow:
    fetch issues → (orchestrator dispatches Devin) → add label → post comment →
    verify PR commits → check CI status.
    """

    def _make_client(self) -> tuple[GitHubClient, MagicMock]:
        session = MagicMock(spec=requests.Session)
        return GitHubClient(session=session, repo="org/repo"), session

    @pytest.mark.unit
    def test_full_issue_remediation_workflow(self):
        client, session = self._make_client()

        issue_response = _mock_response(200, [
            {
                "number": 10,
                "title": "CVE-2024-1234: package X vulnerable",
                "labels": [{"name": "auto-remediate"}, {"name": "severity:high"}],
                "body": "Upgrade package X to >= 2.0",
            },
            {
                "number": 11,
                "title": "CVE-2024-5678: package Y vulnerable",
                "labels": [{"name": "auto-remediate"}, {"name": "severity:medium"}],
                "body": "Upgrade package Y to >= 3.1",
            },
        ])

        label_response = _mock_response(200, {})
        comment_response = _mock_response(201, {})

        commits_response = _mock_response(200, [
            {"sha": "abc123", "commit": {"author": {"name": "devin[bot]"}}},
            {"sha": "def456", "commit": {"author": {"name": "devin[bot]"}}},
        ])

        pr_response = _mock_response(200, {"head": {"sha": "def456"}})
        ci_response = _mock_response(200, {"workflow_runs": [{
            "id": 7777,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2024-06-01T10:00:00Z",
            "updated_at": "2024-06-01T10:05:00Z",
        }]})

        session.get.side_effect = [issue_response, commits_response, pr_response, ci_response]
        session.post.side_effect = [label_response, comment_response]

        issues = client.get_open_issues("auto-remediate")
        assert len(issues) == 2
        assert issues[0].number == 10
        assert "severity:high" in issues[0].labels

        client.add_label(issue_number=10, label="devin:in-progress")

        client.post_comment(issue_number=10, body="Devin session started: sess-abc")

        commits = client.get_pr_commits(pr_number=42)
        assert len(commits) == 2
        assert all(c.author == "devin[bot]" for c in commits)

        ci = client.get_latest_ci_run(pr_number=42)
        assert ci is not None
        assert ci.status == "completed"
        assert ci.conclusion == "success"

        assert session.get.call_count == 4
        assert session.post.call_count == 2

    @pytest.mark.unit
    def test_no_issues_found_workflow(self):
        """Empty issue list — orchestrator has nothing to do."""
        client, session = self._make_client()

        session.get.return_value = _mock_response(200, [])

        issues = client.get_open_issues("auto-remediate")
        assert issues == []

    @pytest.mark.unit
    def test_ci_failure_workflow(self):
        """Simulates: fetch issue → Devin creates PR → CI fails."""
        client, session = self._make_client()

        issue_resp = _mock_response(200, [
            {"number": 15, "title": "Bug fix", "labels": [{"name": "auto-remediate"}], "body": "Fix it"},
        ])
        pr_resp = _mock_response(200, {"head": {"sha": "sha-fail"}})
        ci_resp = _mock_response(200, {"workflow_runs": [{
            "id": 8888,
            "status": "completed",
            "conclusion": "failure",
            "run_started_at": "2024-06-01T10:00:00Z",
            "updated_at": "2024-06-01T10:10:00Z",
        }]})
        comment_resp = _mock_response(201, {})

        session.get.side_effect = [issue_resp, pr_resp, ci_resp]
        session.post.return_value = comment_resp

        issues = client.get_open_issues("auto-remediate")
        assert len(issues) == 1

        ci = client.get_latest_ci_run(pr_number=50)
        assert ci is not None
        assert ci.conclusion == "failure"

        client.post_comment(
            issue_number=15,
            body="CI failed on PR #50 — human review required.",
        )


# ---------------------------------------------------------------------------
# Cross-client orchestration E2E
# ---------------------------------------------------------------------------


class TestCrossClientOrchestration:
    """Simulates the full Stage 4 orchestrator flow using both clients:
    GitHub.get_issues → Devin.create_session → Devin.poll → GitHub.add_label →
    GitHub.post_comment → GitHub.check_ci.
    """

    @pytest.mark.unit
    def test_full_orchestration_flow(self):
        gh_session = MagicMock(spec=requests.Session)
        gh_client = GitHubClient(session=gh_session, repo="org/repo")
        devin_client = DevinClient(api_key="key", org_id="org-test")

        gh_session.get.side_effect = [
            _mock_response(200, [
                {
                    "number": 10,
                    "title": "CVE-2024-1234",
                    "labels": [{"name": "auto-remediate"}, {"name": "complexity:definite"}],
                    "body": "Upgrade dep X",
                },
            ]),
        ]

        devin_post_responses = [
            _mock_response(200, {"session_id": "sess-orch-1"}),
        ]
        devin_get_responses = [
            _mock_response(200, {
                "session_id": "sess-orch-1",
                "status": "completed",
                "cost_usd": 2.00,
                "session_url": "https://app.devin.ai/sessions/sess-orch-1",
                "pr_url": "https://github.com/org/repo/pull/99",
                "output": "Upgraded dep X to v2.0.",
            }),
        ]
        devin_client._session.post = MagicMock(side_effect=devin_post_responses)
        devin_client._session.get = MagicMock(side_effect=devin_get_responses)

        issues = gh_client.get_open_issues("auto-remediate")
        assert len(issues) == 1
        issue = issues[0]

        session_id = devin_client.create_session(
            prompt=f"Fix {issue.title}: {issue.body}",
            repo_url="https://github.com/org/repo",
            issue_id=issue.number,
        )
        assert session_id == "sess-orch-1"

        resp = devin_client.get_session(session_id)
        assert resp.status == "completed"
        assert resp.pr_url == "https://github.com/org/repo/pull/99"

        label_resp = _mock_response(200, {})
        comment_resp = _mock_response(201, {})
        gh_session.post.side_effect = [label_resp, comment_resp]

        gh_client.add_label(issue.number, "devin:completed")
        gh_client.post_comment(
            issue.number,
            f"Devin completed: PR {resp.pr_url} (cost: ${resp.cost_usd:.2f})",
        )

        assert gh_session.post.call_count == 2
        comment_args = gh_session.post.call_args_list[1]
        assert "PR https://github.com/org/repo/pull/99" in comment_args[1]["json"]["body"]

    @pytest.mark.unit
    def test_orchestration_with_api_failure_recovery(self):
        """Devin API fails on first create_session, orchestrator retries with a new call."""
        gh_session = MagicMock(spec=requests.Session)
        gh_client = GitHubClient(session=gh_session, repo="org/repo")
        devin_client = DevinClient(api_key="key", org_id="org-test")

        gh_session.get.return_value = _mock_response(200, [
            {"number": 5, "title": "Fix bug", "labels": [{"name": "auto-remediate"}], "body": "desc"},
        ])

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.headers = {}
        error_resp.text = "Internal Server Error"
        http_error = requests.HTTPError(response=error_resp)

        success_resp = _mock_response(200, {"session_id": "sess-recovered"})

        devin_client._session.post = MagicMock(
            side_effect=[http_error, http_error, success_resp]
        )

        issues = gh_client.get_open_issues("auto-remediate")
        assert len(issues) == 1

        with patch("time.sleep"):
            session_id = devin_client.create_session(
                prompt="Fix bug",
                repo_url="https://github.com/org/repo",
                issue_id=5,
            )
        assert session_id == "sess-recovered"

    @pytest.mark.unit
    def test_orchestration_devin_api_exhausts_retries(self):
        """Devin API fails all retries — orchestrator should log start_failed event."""
        devin_client = DevinClient(api_key="key", org_id="org-test")

        error_resp = MagicMock()
        error_resp.status_code = 503
        error_resp.headers = {}
        error_resp.text = "Service Unavailable"
        http_error = requests.HTTPError(response=error_resp)

        devin_client._session.post = MagicMock(side_effect=http_error)

        with patch("time.sleep"):
            with pytest.raises(DevinAPIError):
                devin_client.create_session(
                    prompt="Fix something",
                    repo_url="https://github.com/org/repo",
                    issue_id=99,
                )

    @pytest.mark.unit
    def test_multiple_issues_batch_processing(self):
        """Orchestrator processes multiple issues sequentially."""
        gh_session = MagicMock(spec=requests.Session)
        gh_client = GitHubClient(session=gh_session, repo="org/repo")
        devin_client = DevinClient(api_key="key", org_id="org-test")

        gh_session.get.return_value = _mock_response(200, [
            {"number": 1, "title": "CVE-1", "labels": [{"name": "auto-remediate"}], "body": "fix 1"},
            {"number": 2, "title": "CVE-2", "labels": [{"name": "auto-remediate"}], "body": "fix 2"},
            {"number": 3, "title": "CVE-3", "labels": [{"name": "auto-remediate"}], "body": "fix 3"},
        ])

        devin_client._session.post = MagicMock(side_effect=[
            _mock_response(200, {"session_id": f"sess-{i}"}) for i in range(1, 4)
        ])
        devin_client._session.get = MagicMock(side_effect=[
            _mock_response(200, {
                "session_id": f"sess-{i}",
                "status": "completed",
                "cost_usd": float(i),
                "session_url": None,
                "pr_url": f"https://github.com/org/repo/pull/{100 + i}",
                "output": f"Fixed CVE-{i}",
            }) for i in range(1, 4)
        ])

        issues = gh_client.get_open_issues("auto-remediate")
        assert len(issues) == 3

        results = []
        for issue in issues:
            sid = devin_client.create_session(
                prompt=f"Fix {issue.title}",
                repo_url="https://github.com/org/repo",
                issue_id=issue.number,
            )
            resp = devin_client.get_session(sid)
            results.append((issue.number, resp.status, resp.pr_url))

        assert len(results) == 3
        assert all(status == "completed" for _, status, _ in results)
        assert results[0] == (1, "completed", "https://github.com/org/repo/pull/101")
        assert results[2] == (3, "completed", "https://github.com/org/repo/pull/103")
