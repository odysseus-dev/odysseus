"""Regression tests: LLM retry backoff and Retry-After header handling.

Tests for:
- _retry_delay: exponential backoff formula (T1)
- _retry_delay: Retry-After integer header wins over backoff (T1)
- stream_llm: retries on 429/503 before yielding error SSE (T3)
- llm_call (sync): retries on transient 429/503 (T4)
"""
import asyncio
import time
import types
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

# Helper to run async generators synchronously for testing
def _run_async_gen(agen):
    """Collect all items from an async generator using asyncio.run."""
    async def _collect():
        return [item async for item in agen]
    return asyncio.run(_collect())


# ---------------------------------------------------------------------------
# _retry_delay unit tests (T1)
# ---------------------------------------------------------------------------

def test_exponential_backoff_delays():
    from src.llm_core import _retry_delay
    # attempt 1 → 1.0s, attempt 2 → 2.0s, attempt 3 → 4.0s
    assert _retry_delay(1) == pytest.approx(1.0, abs=0.01)
    assert _retry_delay(2) == pytest.approx(2.0, abs=0.01)
    assert _retry_delay(3) == pytest.approx(4.0, abs=0.01)


def test_backoff_capped_at_30s():
    from src.llm_core import _retry_delay
    # attempt 10 → 1024s uncapped, but must be capped at 30s
    assert _retry_delay(10) <= 30.0


def test_retry_after_header_respected():
    """Retry-After integer string wins over exponential backoff."""
    from src.llm_core import _retry_delay
    mock_resp = MagicMock()
    mock_resp.headers = {"Retry-After": "15"}
    result = _retry_delay(1, mock_resp)
    assert result == pytest.approx(15.0, abs=0.5)


def test_retry_after_header_absent_falls_back_to_backoff():
    """When Retry-After is absent, uses exponential backoff."""
    from src.llm_core import _retry_delay
    mock_resp = MagicMock()
    mock_resp.headers = {}
    result = _retry_delay(1, mock_resp)
    assert result == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# stream_llm retry test (T3)
# ---------------------------------------------------------------------------

def test_stream_llm_retries_on_429(monkeypatch):
    """stream_llm must retry once on 429 before yielding an error event."""
    from src import llm_core

    call_count = [0]
    sleep_calls = []

    class _FakeResp:
        def __init__(self, status, body=b"rate limited"):
            self.status_code = status
            self._body = body
        async def aread(self):
            return self._body
        async def aiter_lines(self):
            # yields nothing — status is checked before iteration
            return
            yield  # make it an async generator

    class _FakeStream:
        def __init__(self, resp):
            self._resp = resp
        async def __aenter__(self):
            call_count[0] += 1
            return self._resp
        async def __aexit__(self, *a):
            pass

    responses = [_FakeResp(429), _FakeResp(429), _FakeResp(429)]
    resp_iter = iter(responses)

    def _fake_stream_cm(*a, **kw):
        return _FakeStream(next(resp_iter))

    async def _fake_sleep(s):
        sleep_calls.append(s)

    fake_client = MagicMock()
    fake_client.stream.side_effect = _fake_stream_cm

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: fake_client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core.asyncio, "sleep", _fake_sleep)

    chunks = _run_async_gen(llm_core.stream_llm(
        "http://localhost:11434/v1/chat/completions",
        "llama3",
        [{"role": "user", "content": "hi"}],
    ))

    # Must have slept exactly STREAM_MAX_RETRIES times (finding TST-002: assert
    # exact count rather than the loose truthy check).
    assert len(sleep_calls) == llm_core.LLMConfig.STREAM_MAX_RETRIES, (
        f"Expected {llm_core.LLMConfig.STREAM_MAX_RETRIES} sleep(s) for retries, got {len(sleep_calls)}"
    )
    # Final chunk must be an error event with a specific status field (TST-002).
    # The fake upstream returns 429; after exhausting STREAM_MAX_RETRIES the
    # error SSE should carry that status code.
    error_chunks = [c for c in chunks if "event: error" in c]
    assert error_chunks, "Must end with error event after exhausting retries"
    import json as _json
    last_data_line = [line for line in error_chunks[-1].splitlines() if line.startswith("data: ")]
    assert last_data_line, f"Error SSE must have a data: line, got: {error_chunks[-1]}"
    error_payload = _json.loads(last_data_line[0][len("data: "):])
    assert "status" in error_payload, f"Error SSE data must contain 'status', got: {error_payload}"
    assert error_payload["status"] == 429, (
        f"Error SSE status should match the upstream 429 the fake returns, got: {error_payload['status']}"
    )


# ---------------------------------------------------------------------------
# llm_call (sync) retry test (T4)
# ---------------------------------------------------------------------------

def test_sync_llm_call_retries_on_transient_error(monkeypatch):
    """sync llm_call must retry on 429/503 before raising."""
    from src import llm_core

    # Clear module-level cache so a prior cache entry cannot short-circuit
    # llm_call and produce a false-pass on the retry assertion (finding TST-001).
    with llm_core._cache_lock:
        llm_core._response_cache.clear()
    # Ensure the host-dead state does not pre-empt the POST attempts.
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)

    call_count = [0]

    def _fake_post(*a, **kw):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 429
        resp.is_success = False
        resp.text = "rate limited"
        resp.headers = {}
        return resp

    monkeypatch.setattr(llm_core.httpx, "post", _fake_post)
    monkeypatch.setattr(llm_core.time, "sleep", lambda s: None)

    with pytest.raises(Exception):
        llm_core.llm_call(
            "http://localhost:11434/v1/chat/completions",
            "llama3",
            [{"role": "user", "content": "hi"}],
        )

    # Must have been called exactly MAX_RETRIES times (finding TST-002: assert
    # exact count rather than the loose > 1 which could pass after only 2 of 3).
    assert call_count[0] == llm_core.LLMConfig.MAX_RETRIES, (
        f"Expected exactly {llm_core.LLMConfig.MAX_RETRIES} calls (retry), got {call_count[0]}"
    )
