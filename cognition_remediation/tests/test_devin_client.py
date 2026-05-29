import pytest
import requests
from unittest.mock import MagicMock, patch

from app.devin_client import DevinClient, DevinAPIError, SessionResponse


def _make_client() -> DevinClient:
    return DevinClient(api_key="test-key", org_id="org-test")


def _mock_response(status_code: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body
    r.raise_for_status.return_value = None
    return r


@pytest.mark.unit
def test_create_session_returns_session_id():
    client = _make_client()
    client._session.post = MagicMock(
        return_value=_mock_response(200, {"session_id": "sess-abc"})
    )
    result = client.create_session("Fix CVE", "https://github.com/org/repo", issue_id=42)
    assert result == "sess-abc"


@pytest.mark.unit
def test_create_session_sends_correct_payload():
    client = _make_client()
    mock_post = MagicMock(return_value=_mock_response(200, {"session_id": "sess-xyz"}))
    client._session.post = mock_post

    client.create_session("Fix CVE", "https://github.com/org/repo", issue_id=7)

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["prompt"] == "Fix CVE"
    assert payload["repo_url"] == "https://github.com/org/repo"
    assert payload["metadata"]["issue_id"] == 7


@pytest.mark.unit
def test_get_session_maps_all_fields():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "completed",
        "cost_usd": 1.23,
        "session_url": "https://app.devin.ai/sessions/sess-abc",
        "pr_url": "https://github.com/org/repo/pull/5",
        "output": "Fixed the CVE.",
    }))

    result = client.get_session("sess-abc")

    assert isinstance(result, SessionResponse)
    assert result.session_id == "sess-abc"
    assert result.status == "completed"
    assert result.cost_usd == 1.23
    assert result.pr_url == "https://github.com/org/repo/pull/5"
    assert result.output == "Fixed the CVE."


@pytest.mark.unit
def test_get_session_handles_null_optional_fields():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "running",
        "cost_usd": None,
        "session_url": None,
        "pr_url": None,
        "output": None,
    }))

    result = client.get_session("sess-abc")
    assert result.cost_usd is None
    assert result.pr_url is None


@pytest.mark.unit
def test_create_session_raises_devin_api_error_on_5xx():
    client = _make_client()
    r = MagicMock()
    r.status_code = 500
    r.headers = {}
    exc = requests.HTTPError(response=r)
    client._session.post = MagicMock(side_effect=exc)

    with patch("time.sleep"):
        with pytest.raises(DevinAPIError):
            client.create_session("Fix CVE", "https://github.com/org/repo", issue_id=1)


@pytest.mark.unit
def test_get_session_raises_devin_api_error_on_5xx():
    client = _make_client()
    r = MagicMock()
    r.status_code = 503
    r.headers = {}
    exc = requests.HTTPError(response=r)
    client._session.get = MagicMock(side_effect=exc)

    with patch("time.sleep"):
        with pytest.raises(DevinAPIError):
            client.get_session("sess-abc")


@pytest.mark.skip(reason="Costs money — documents the real endpoint only")
@pytest.mark.integration
def test_create_session_real_devin_api():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ["DEVIN_API_KEY"]
    client = DevinClient(api_key=api_key)
    # Real call would create a chargeable session — never run in CI
    session_id = client.create_session(
        prompt="Print hello world to stdout.",
        repo_url="https://github.com/hchang19/superset",
        issue_id=0,
    )
    assert session_id
