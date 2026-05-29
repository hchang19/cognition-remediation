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
def test_get_session_maps_v3_fields():
    """v3 API: status=exit → completed, pull_requests array, acus_consumed, messages."""
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "exit",
        "status_detail": None,
        "acus_consumed": 1.23,
        "url": "https://app.devin.ai/sessions/sess-abc",
        "pull_requests": [{"url": "https://github.com/org/repo/pull/5"}],
        "messages": [
            {"type": "user", "message": "Fix the CVE."},
            {"type": "assistant", "message": "Fixed the CVE."},
        ],
    }))

    result = client.get_session("sess-abc")

    assert isinstance(result, SessionResponse)
    assert result.session_id == "sess-abc"
    assert result.status == "completed"          # exit → completed
    assert result.cost_usd == 1.23              # acus_consumed
    assert result.pr_url == "https://github.com/org/repo/pull/5"
    assert result.output == "Fixed the CVE."    # last assistant message


@pytest.mark.unit
def test_get_session_handles_null_optional_fields():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "running",
        "status_detail": "working",
    }))

    result = client.get_session("sess-abc")
    assert result.status == "running"
    assert result.cost_usd is None
    assert result.pr_url is None


@pytest.mark.unit
def test_get_session_normalizes_pending_waiting_for_user():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "running",
        "status_detail": "waiting_for_user",
    }))

    result = client.get_session("sess-abc")
    assert result.status == "pending"
    assert result.status_detail == "waiting_for_user"


@pytest.mark.unit
def test_get_session_normalizes_suspended_to_blocked():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "suspended",
        "status_detail": "usage_limit_exceeded",
        "acus_consumed": 5.0,
    }))

    result = client.get_session("sess-abc")
    assert result.status == "blocked"
    assert result.cost_usd == 5.0


@pytest.mark.unit
def test_get_session_normalizes_suspended_inactivity_to_pending():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "suspended",
        "status_detail": "inactivity",
    }))

    result = client.get_session("sess-abc")
    assert result.status == "pending"


@pytest.mark.unit
def test_get_session_normalizes_error_to_failed():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "error",
    }))

    result = client.get_session("sess-abc")
    assert result.status == "failed"


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


@pytest.mark.unit
def test_terminate_session_calls_delete():
    client = _make_client()
    r = _mock_response(200, {})
    client._session.delete = MagicMock(return_value=r)

    client.terminate_session("sess-abc")

    args, _ = client._session.delete.call_args
    assert args[0].endswith("/sessions/sess-abc")


@pytest.mark.unit
def test_terminate_session_raises_devin_api_error_on_failure():
    client = _make_client()
    r = MagicMock()
    r.status_code = 500
    r.headers = {}
    exc = requests.HTTPError(response=r)
    client._session.delete = MagicMock(side_effect=exc)

    with pytest.raises(DevinAPIError):
        client.terminate_session("sess-abc")


@pytest.mark.unit
def test_create_session_raises_on_connection_error():
    client = _make_client()
    client._session.post = MagicMock(side_effect=requests.ConnectionError("DNS failure"))

    with patch("time.sleep"):
        with pytest.raises(DevinAPIError):
            client.create_session("Fix CVE", "https://github.com/org/repo", issue_id=1)


@pytest.mark.unit
def test_get_session_raises_on_connection_error():
    client = _make_client()
    client._session.get = MagicMock(side_effect=requests.ConnectionError("DNS failure"))

    with patch("time.sleep"):
        with pytest.raises(DevinAPIError):
            client.get_session("sess-abc")


@pytest.mark.unit
def test_create_session_uses_correct_url():
    client = _make_client()
    client._session.post = MagicMock(
        return_value=_mock_response(200, {"session_id": "sess-abc"})
    )

    client.create_session("Fix CVE", "https://github.com/org/repo", issue_id=1)

    args, _ = client._session.post.call_args
    assert args[0] == "https://api.devin.ai/v3/organizations/org-test/sessions"


@pytest.mark.unit
def test_get_session_uses_correct_url():
    client = _make_client()
    client._session.get = MagicMock(return_value=_mock_response(200, {
        "session_id": "sess-abc",
        "status": "running",
    }))

    client.get_session("sess-abc")

    args, _ = client._session.get.call_args
    assert args[0] == "https://api.devin.ai/v3/organizations/org-test/sessions/sess-abc"


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
