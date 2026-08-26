from __future__ import annotations

from flashfusion.eval import benchmark


class TooManyRequestsResponseError(Exception):
    pass


class _RateLimitedError(Exception):
    def __init__(self, retry_after: str) -> None:
        self.headers = {"retry-after": retry_after}


def test_query_retry_recognizes_openrouter_rate_limit_exception() -> None:
    assert benchmark._is_retryable_query_error(TooManyRequestsResponseError())


def test_query_retry_prefers_bounded_provider_delay() -> None:
    error = _RateLimitedError("45")

    assert benchmark._query_retry_delay_seconds(error, attempt=0) == 30.0