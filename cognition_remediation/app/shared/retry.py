"""Exponential backoff decorator for HTTP/network calls."""

from __future__ import annotations

import functools
import time
from typing import Callable, TypeVar

import requests

F = TypeVar("F", bound=Callable)

_RETRYABLE_STATUS = {500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return response is not None and response.status_code in _RETRYABLE_STATUS
    return False


def with_retry(max_attempts: int = 3, base_delay: float = 1.0) -> Callable[[F], F]:
    """Retry on transient HTTP (5xx) and connection errors with exponential backoff.

    Delays: base_delay, base_delay * 2, base_delay * 4 (default 1s, 2s, 4s).
    Raises the original exception after exhausting attempts.
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
                    time.sleep(base_delay * (2**attempt))
            assert last_exc is not None
            raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator
