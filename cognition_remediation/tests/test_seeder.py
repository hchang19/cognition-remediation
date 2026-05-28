import pytest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.seed_issues import (
    _already_seeded,
    _collect_labels,
    _extract_idempotency_key,
    ensure_labels,
    fetch_existing_issue_bodies,
    load_issues,
)


SAMPLE_BODY = (
    "Some issue text.\n"
    '<!-- cognition-meta {"idempotency_key": "cve-urllib3-2024-01", "complexity": "definite"} -->\n'
    "More text."
)


@pytest.mark.unit
def test_extract_idempotency_key_valid():
    assert _extract_idempotency_key(SAMPLE_BODY) == "cve-urllib3-2024-01"


@pytest.mark.unit
def test_extract_idempotency_key_missing():
    assert _extract_idempotency_key("No meta block here") is None


@pytest.mark.unit
def test_already_seeded_true():
    bodies = ["some body", SAMPLE_BODY, "other body"]
    assert _already_seeded("cve-urllib3-2024-01", bodies) is True


@pytest.mark.unit
def test_already_seeded_false():
    bodies = ["some body", "other body"]
    assert _already_seeded("cve-urllib3-2024-01", bodies) is False


@pytest.mark.unit
def test_collect_labels_deduplicates():
    issues = [
        {"labels": ["auto-remediate", "complexity:definite"]},
        {"labels": ["auto-remediate", "type:vulnerability"]},
    ]
    labels = _collect_labels(issues)
    assert len(labels) == len(set(labels))
    assert "auto-remediate" in labels
    assert len(labels) == 3


@pytest.mark.unit
def test_fetch_existing_issue_bodies_filters_prs(mock_session):
    items = [
        {"body": "issue body", "number": 1},
        {"body": "pr body", "number": 2, "pull_request": {"url": "https://..."}},
    ]
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = items
    response.raise_for_status.return_value = None
    mock_session.get.return_value = response

    bodies = fetch_existing_issue_bodies(mock_session, "owner/repo")
    assert "issue body" in bodies
    assert "pr body" not in bodies


@pytest.mark.unit
def test_fetch_existing_issue_bodies_paginates(mock_session):
    page1 = [{"body": f"body-{i}", "number": i} for i in range(100)]
    page2 = [{"body": "last-body", "number": 100}]

    def make_response(items):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = items
        r.raise_for_status.return_value = None
        return r

    mock_session.get.side_effect = [make_response(page1), make_response(page2)]

    bodies = fetch_existing_issue_bodies(mock_session, "owner/repo")
    assert len(bodies) == 101
    assert "last-body" in bodies


@pytest.mark.unit
def test_ensure_labels_treats_422_as_success(mock_session):
    response = MagicMock()
    response.status_code = 422
    response.raise_for_status.return_value = None
    mock_session.post.return_value = response
    ensure_labels(mock_session, "owner/repo", ["auto-remediate"])


@pytest.mark.unit
def test_load_issues_raises_on_non_list(tmp_path):
    bad_yaml = tmp_path / "issues.yml"
    bad_yaml.write_text("issues: not-a-list\n")
    with pytest.raises(ValueError, match="must be a list"):
        load_issues(bad_yaml)


@pytest.mark.integration
def test_fetch_existing_issue_bodies_real_github():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO", "hchang19/superset")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")

    from app.shared.github_session import github_session
    session = github_session(token)
    bodies = fetch_existing_issue_bodies(session, repo)
    assert isinstance(bodies, list)
