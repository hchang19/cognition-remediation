"""Exponential backoff decorator for HTTP/network calls.

Retries on transient HTTP failures (5xx) and GitHub rate limits (403 with
`X-RateLimit-Remaining: 0` or `Retry-After`, and 429). Honors `Retry-After`
when the server provides it; otherwise uses exponential backoff.
"""

from __future__ import annotations

import functools
import time
from typing import Callable, TypeVar

import requests

F = TypeVar("F", bound=Callable)

_RETRYABLE_STATUS = {500, 502, 503, 504, 429}
_MAX_RETRY_AFTER_SECONDS = 120.0  # cap server-suggested delays to avoid hangs


def _is_rate_limited(response: requests.Response) -> bool:
    """GitHub 403 with rate-limit signals — distinct from auth/permission 403s."""
    if response.status_code != 403:
        return False
    if response.headers.get("Retry-After"):
        return True
    return response.headers.get("X-RateLimit-Remaining") == "0"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        if response is None:
            return False
        if response.status_code in _RETRYABLE_STATUS:
            return True
        return _is_rate_limited(response)
    return False


def _delay_for(exc: BaseException, attempt: int, base_delay: float) -> float:
    """Honor server-provided Retry-After when present; otherwise exponential backoff."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass  # malformed header — fall through to exponential backoff
    return base_delay * (2**attempt)


def with_retry(max_attempts: int = 3, base_delay: float = 1.0) -> Callable[[F], F]:
    """Retry on transient HTTP failures and GitHub rate limits.

    Retried conditions:
    - ConnectionError
    - HTTP 5xx (500, 502, 503, 504)
    - HTTP 429 (too many requests)
    - HTTP 403 when accompanied by `Retry-After` or `X-RateLimit-Remaining: 0`

    Default delays: 1s, 2s, 4s. `Retry-After` headers override the backoff
    schedule, capped at 120s to avoid unbounded hangs. Raises the original
    exception after exhausting attempts.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except (requests.ConnectionError, requests.HTTPError) as exc:
                    last_exc = exc
                    if not _is_retryable(exc) or attempt == max_attempts - 1:
                        raise
                    time.sleep(_delay_for(exc, attempt, base_delay))
            assert last_exc is not None
            raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator
