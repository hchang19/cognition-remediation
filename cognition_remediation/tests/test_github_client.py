import pytest
from unittest.mock import MagicMock, call
import requests

from app.github_client import GitHubClient, Issue, Commit, CIRun
from app.shared.github_session import github_session


def _make_client(repo: str = "owner/repo") -> tuple[GitHubClient, MagicMock]:
    session = MagicMock(spec=requests.Session)
    client = GitHubClient(session=session, repo=repo)
    return client, session


def _mock_get(session: MagicMock, responses: list[dict]) -> None:
    mocks = []
    for body in responses:
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = body
        r.raise_for_status.return_value = None
        mocks.append(r)
    session.get.side_effect = mocks


@pytest.mark.unit
def test_get_open_issues_filters_prs():
    client, session = _make_client()
    _mock_get(session, [[
        {"number": 1, "title": "Issue", "labels": [], "body": ""},
        {"number": 2, "title": "PR", "labels": [], "body": "", "pull_request": {"url": "..."}},
    ]])

    results = client.get_open_issues("auto-remediate")
    assert len(results) == 1
    assert results[0].number == 1


@pytest.mark.unit
def test_get_open_issues_paginates():
    client, session = _make_client()
    page1 = [{"number": i, "title": f"Issue {i}", "labels": [], "body": ""} for i in range(100)]
    page2 = [{"number": 100, "title": "Issue 100", "labels": [], "body": ""}]
    _mock_get(session, [page1, page2])

    results = client.get_open_issues("auto-remediate")
    assert len(results) == 101


@pytest.mark.unit
def test_get_open_issues_maps_labels():
    client, session = _make_client()
    _mock_get(session, [[
        {"number": 1, "title": "T", "labels": [{"name": "auto-remediate"}, {"name": "complexity:definite"}], "body": ""},
    ]])

    results = client.get_open_issues("auto-remediate")
    assert results[0].labels == ["auto-remediate", "complexity:definite"]


@pytest.mark.unit
def test_get_pr_commits_maps_sha_and_author():
    client, session = _make_client()
    _mock_get(session, [[
        {"sha": "abc123", "commit": {"author": {"name": "Alice"}}},
        {"sha": "def456", "commit": {"author": {"name": "Bob"}}},
    ]])

    results = client.get_pr_commits(pr_number=5)
    assert len(results) == 2
    assert results[0] == Commit(sha="abc123", author="Alice")
    assert results[1] == Commit(sha="def456", author="Bob")


@pytest.mark.unit
def test_get_latest_ci_run_returns_none_when_no_runs():
    client, session = _make_client()
    _mock_get(session, [
        {"head": {"sha": "abc123"}},       # PR response
        {"workflow_runs": []},              # Actions runs
    ])

    result = client.get_latest_ci_run(pr_number=1)
    assert result is None


@pytest.mark.unit
def test_get_latest_ci_run_maps_fields():
    client, session = _make_client()
    _mock_get(session, [
        {"head": {"sha": "abc123"}},
        {"workflow_runs": [{
            "id": 999,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T01:00:00Z",
        }]},
    ])

    result = client.get_latest_ci_run(pr_number=1)
    assert isinstance(result, CIRun)
    assert result.run_id == 999
    assert result.status == "completed"
    assert result.conclusion == "success"
    assert result.completed_at == "2024-01-01T01:00:00Z"


@pytest.mark.unit
def test_add_label_sends_correct_payload():
    client, session = _make_client()
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    session.post.return_value = r

    client.add_label(issue_number=7, label="auto-remediate")

    _, kwargs = session.post.call_args
    assert kwargs["json"] == {"labels": ["auto-remediate"]}


@pytest.mark.unit
def test_post_comment_sends_correct_payload():
    client, session = _make_client()
    r = MagicMock()
    r.status_code = 201
    r.raise_for_status.return_value = None
    session.post.return_value = r

    client.post_comment(issue_number=3, body="Devin is working on this.")

    _, kwargs = session.post.call_args
    assert kwargs["json"] == {"body": "Devin is working on this."}


@pytest.mark.unit
def test_get_latest_ci_run_in_progress_has_no_completed_at():
    client, session = _make_client()
    _mock_get(session, [
        {"head": {"sha": "abc123"}},
        {"workflow_runs": [{
            "id": 888,
            "status": "in_progress",
            "conclusion": None,
            "run_started_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:30:00Z",
        }]},
    ])

    result = client.get_latest_ci_run(pr_number=1)
    assert isinstance(result, CIRun)
    assert result.status == "in_progress"
    assert result.conclusion is None
    assert result.completed_at is None


@pytest.mark.unit
def test_get_open_issues_empty_body_defaults_to_empty_string():
    client, session = _make_client()
    _mock_get(session, [[
        {"number": 1, "title": "No body", "labels": [], "body": None},
    ]])

    results = client.get_open_issues("auto-remediate")
    assert results[0].body == ""


@pytest.mark.unit
def test_add_label_uses_correct_url():
    client, session = _make_client("owner/repo")
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    session.post.return_value = r

    client.add_label(issue_number=42, label="auto-remediate")

    args, _ = session.post.call_args
    assert args[0] == "https://api.github.com/repos/owner/repo/issues/42/labels"


@pytest.mark.unit
def test_post_comment_uses_correct_url():
    client, session = _make_client("owner/repo")
    r = MagicMock()
    r.status_code = 201
    r.raise_for_status.return_value = None
    session.post.return_value = r

    client.post_comment(issue_number=7, body="test comment")

    args, _ = session.post.call_args
    assert args[0] == "https://api.github.com/repos/owner/repo/issues/7/comments"


@pytest.mark.unit
def test_get_pr_commits_uses_correct_url():
    client, session = _make_client("owner/repo")
    _mock_get(session, [[]])

    client.get_pr_commits(pr_number=10)

    args, _ = session.get.call_args
    assert args[0] == "https://api.github.com/repos/owner/repo/pulls/10/commits"


@pytest.mark.unit
def test_merge_pr_calls_correct_url():
    client, session = _make_client("owner/repo")
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    session.put.return_value = r

    client.merge_pr(pr_number=42)

    args, _ = session.put.call_args
    assert args[0] == "https://api.github.com/repos/owner/repo/pulls/42/merge"


@pytest.mark.unit
def test_merge_pr_uses_squash_by_default():
    client, session = _make_client()
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    session.put.return_value = r

    client.merge_pr(pr_number=7)

    _, kwargs = session.put.call_args
    assert kwargs["json"]["merge_method"] == "squash"


@pytest.mark.unit
def test_merge_pr_raises_on_api_error():
    client, session = _make_client()
    r = MagicMock()
    r.status_code = 405
    r.headers = {}
    exc = requests.HTTPError(response=r)
    session.put.return_value = MagicMock(raise_for_status=MagicMock(side_effect=exc))

    with pytest.raises(requests.HTTPError):
        client.merge_pr(pr_number=1)


@pytest.mark.integration
def test_get_open_issues_real_github():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")

    from app.shared.github_session import github_session as make_session
    session = make_session(token)
    client = GitHubClient(session=session, repo=repo)
    issues = client.get_open_issues("auto-remediate")
    assert isinstance(issues, list)
