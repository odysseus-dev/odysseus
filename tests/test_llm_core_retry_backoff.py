"""Unit tests for llm_core._retry_delay.

llm_core defers its `from src.database import ...` to inside a function, so the
module imports cleanly at collection even though conftest stubs src.database —
a plain module-level import is safe here.

_retry_delay replaced the previous flat LLMConfig.RETRY_DELAY (0.5s) sleeps in
llm_call_async, adding exponential backoff and Retry-After-header support.
"""
from types import SimpleNamespace

import pytest

from src.llm_core import _retry_delay


def _resp(retry_after):
    """A minimal stand-in for an httpx.Response carrying a Retry-After header."""
    return SimpleNamespace(headers={"Retry-After": retry_after} if retry_after is not None else {})


def test_exponential_backoff_sequence():
    assert _retry_delay(1) == pytest.approx(1.0)
    assert _retry_delay(2) == pytest.approx(2.0)
    assert _retry_delay(3) == pytest.approx(4.0)
    assert _retry_delay(4) == pytest.approx(8.0)


def test_backoff_capped_at_30s():
    assert _retry_delay(10) == 30.0
    assert _retry_delay(100) == 30.0


def test_retry_after_integer_header_wins_over_backoff():
    # Attempt 1's backoff would be 1.0s, but the server asked for 5s.
    assert _retry_delay(1, _resp("5")) == pytest.approx(5.0)


def test_retry_after_capped_at_60s():
    assert _retry_delay(1, _resp("9999")) == 60.0


def test_retry_after_absent_falls_back_to_backoff():
    assert _retry_delay(3, _resp(None)) == pytest.approx(4.0)


def test_retry_after_garbage_falls_back_to_backoff():
    # Non-numeric, non-HTTP-date value must not raise — fall back to backoff.
    assert _retry_delay(2, _resp("soon-ish")) == pytest.approx(2.0)


def test_missing_headers_attr_is_tolerated():
    # A response object without a usable headers mapping must not raise.
    class _NoHeaders:
        pass
    assert _retry_delay(1, _NoHeaders()) == pytest.approx(1.0)
