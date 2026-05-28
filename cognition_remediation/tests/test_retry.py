import pytest
import requests
from unittest.mock import MagicMock, patch

from app.shared.retry import with_retry


def _http_error(status_code: int, headers: dict | None = None) -> requests.HTTPError:
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    return requests.HTTPError(response=r)


@pytest.mark.unit
def test_success_on_first_attempt():
    call_count = 0

    @with_retry()
    def fn():
        nonlocal call_count
        call_count += 1
        return "ok"

    assert fn() == "ok"
    assert call_count == 1


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429])
def test_retries_on_transient_errors(status_code):
    exc = _http_error(status_code)
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise exc
        return "ok"

    with patch("time.sleep"):
        result = fn()

    assert result == "ok"
    assert call_count == 3


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [400, 404])
def test_no_retry_on_client_errors(status_code):
    exc = _http_error(status_code)
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 1


@pytest.mark.unit
def test_raises_after_exhausting_attempts():
    exc = _http_error(500)
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with patch("time.sleep"), pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 3


@pytest.mark.unit
def test_retry_after_header_honored():
    exc = _http_error(500, headers={"Retry-After": "5"})
    sleep_calls: list[float] = []

    @with_retry(max_attempts=2, base_delay=1.0)
    def fn():
        raise exc

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(requests.HTTPError):
            fn()

    assert sleep_calls[0] == 5.0


@pytest.mark.unit
def test_retry_after_header_capped_at_120s():
    exc = _http_error(500, headers={"Retry-After": "999"})
    sleep_calls: list[float] = []

    @with_retry(max_attempts=2, base_delay=1.0)
    def fn():
        raise exc

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(requests.HTTPError):
            fn()

    assert sleep_calls[0] == 120.0


@pytest.mark.unit
def test_rate_limit_403_with_remaining_zero_retries():
    exc = _http_error(403, headers={"X-RateLimit-Remaining": "0"})
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with patch("time.sleep"), pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 3


@pytest.mark.unit
def test_plain_403_does_not_retry():
    exc = _http_error(403)  # no rate-limit headers
    call_count = 0

    @with_retry(max_attempts=3, base_delay=0)
    def fn():
        nonlocal call_count
        call_count += 1
        raise exc

    with pytest.raises(requests.HTTPError):
        fn()

    assert call_count == 1


@pytest.mark.integration
def test_github_rate_limit_endpoint_succeeds():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")

    from app.shared.github_session import github_session

    session = github_session(token)

    @with_retry()
    def get_rate_limit():
        r = session.get("https://api.github.com/rate_limit")
        r.raise_for_status()
        return r.json()

    data = get_rate_limit()
    assert "rate" in data
    assert "limit" in data["rate"]
