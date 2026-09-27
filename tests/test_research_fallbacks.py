"""Deep-research resilience against free-tier limits.

1. 429 → wait for Retry-After (research opts in via rate_limit_retries; chat unchanged).
2. research_model_fallbacks chain with per-candidate cooldown.
3. `limited` candidates: serialized, prompt trimmed to max_input_chars, max_tokens capped.
"""
import asyncio
import email.utils
import time

import pytest
from fastapi import HTTPException

llm_core = pytest.importorskip("src.llm_core")
deep_research = pytest.importorskip("src.deep_research")


# ── 1. rate-limit backoff ────────────────────────────────────────────────────

def test_retry_after_parsing():
    assert llm_core._retry_after_seconds("7") == 7.0
    assert llm_core._retry_after_seconds(None) is None
    assert llm_core._retry_after_seconds("garbage") is None
    future = email.utils.formatdate(time.time() + 20, usegmt=True)
    assert 10 <= llm_core._retry_after_seconds(future) <= 21


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.is_success = status == 200
        self.headers = headers or {}
        self.text = "rate limited" if status == 429 else "{}"

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


def _drive(monkeypatch, statuses, **kw):
    seq = list(statuses)
    sleeps = []

    async def fake_post(client, url, headers, json=None, timeout=None):
        return seq.pop(0)

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core.asyncio, "sleep", fake_sleep)
    out = asyncio.run(llm_core.llm_call_async(
        "https://api.example.test/v1", "m", [{"role": "user", "content": f"hi {time.time()}"}], **kw))
    return out, sleeps


def test_research_waits_for_retry_after(monkeypatch):
    out, sleeps = _drive(monkeypatch, [_Resp(429, {"retry-after": "7"}), _Resp(200)], rate_limit_retries=2)
    assert out == "ok" and sleeps == [7.0]


def test_research_backoff_without_header_and_cap(monkeypatch):
    out, sleeps = _drive(monkeypatch, [_Resp(429), _Resp(429, {"retry-after": "600"}), _Resp(200)], rate_limit_retries=2)
    assert out == "ok" and sleeps == [llm_core.RATE_LIMIT_BACKOFF[0], llm_core.RATE_LIMIT_MAX_WAIT]


def test_chat_default_keeps_fast_retry(monkeypatch):
    out, sleeps = _drive(monkeypatch, [_Resp(429, {"retry-after": "30"}), _Resp(200)])
    assert out == "ok" and sleeps == [llm_core.LLMConfig.RETRY_DELAY]


# ── 2 + 3. fallback chain, cooldown, limited mode ────────────────────────────

def _researcher(fallbacks):
    return deep_research.DeepResearcher(llm_endpoint="u0", llm_model="primary", llm_fallbacks=fallbacks)


def _patch_calls(monkeypatch, behaviour):
    calls = []

    async def fake_call(url, model, messages, **kw):
        calls.append({"model": model, "messages": messages, **kw})
        r = behaviour(model)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    return calls


FB = [{"url": "u1", "model": "backup", "headers": {}, "limited": False, "max_input_chars": None, "max_output_tokens": None},
      {"url": "u2", "model": "free", "headers": {}, "limited": True, "max_input_chars": 1000, "max_output_tokens": 256}]


def test_falls_back_and_cools_down_failed_primary(monkeypatch):
    calls = _patch_calls(monkeypatch, lambda m: HTTPException(429, "limit") if m == "primary" else f"answer from {m}")
    r = _researcher(FB)
    assert asyncio.run(r._llm([{"role": "user", "content": "q"}])) == "answer from backup"
    assert [c["model"] for c in calls] == ["primary", "backup"]
    assert all(c["rate_limit_retries"] == 2 for c in calls)
    calls.clear()
    asyncio.run(r._llm([{"role": "user", "content": "q2"}]))
    assert [c["model"] for c in calls] == ["backup"]  # primary skipped while cooling down


def test_limited_candidate_gets_trimmed_prompt_and_capped_tokens(monkeypatch):
    calls = _patch_calls(monkeypatch, lambda m: "ok" if m == "free" else HTTPException(402, "no balance"))
    r = _researcher(FB)
    long_msg = [{"role": "system", "content": "s" * 100}, {"role": "user", "content": "x" * 5000}]
    assert asyncio.run(r._llm(long_msg, max_tokens=8000)) == "ok"
    free = calls[-1]
    assert free["model"] == "free" and free["max_tokens"] == 256
    assert sum(len(m["content"]) for m in free["messages"]) <= 1000
    assert free["messages"][0]["content"] == "s" * 100  # only the longest message is trimmed
    assert len(long_msg[1]["content"]) == 5000  # caller's messages untouched


def test_413_does_not_cool_down_candidate(monkeypatch):
    _patch_calls(monkeypatch, lambda m: HTTPException(413, "too large") if m == "primary" else "ok")
    r = _researcher(FB)
    asyncio.run(r._llm([{"role": "user", "content": "q"}]))
    assert 0 not in r._llm_cooldown


def test_all_fail_raises_last_error(monkeypatch):
    _patch_calls(monkeypatch, lambda m: HTTPException(503, m))
    with pytest.raises(HTTPException):
        asyncio.run(_researcher(FB)._llm([{"role": "user", "content": "q"}]))


def test_limited_calls_are_serialized(monkeypatch):
    active, peak = [0], [0]

    async def fake_call(url, model, messages, **kw):
        if model != "free":
            raise HTTPException(429, "x")
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        return "ok"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    r = _researcher([FB[1]])

    async def run():
        await asyncio.gather(*[r._llm([{"role": "user", "content": str(i)}]) for i in range(5)])

    asyncio.run(run())
    assert peak[0] == 1


def test_resolver_returns_options(monkeypatch):
    er = pytest.importorskip("src.endpoint_resolver")
    import src.settings as settings
    monkeypatch.setattr(settings, "get_setting", lambda k, d=None: [
        {"endpoint_id": "e1", "model": "m1", "limited": True, "max_input_chars": "12000", "max_output_tokens": 2048},
        {"endpoint_id": "missing", "model": "m2"}])
    monkeypatch.setattr(er, "resolve_endpoint_by_id", lambda eid, model, owner=None: ("https://x/v1/chat", model, {"a": "b"}) if eid == "e1" else None)
    out = er.resolve_research_fallback_candidates()
    assert out == [{"url": "https://x/v1/chat", "model": "m1", "headers": {"a": "b"},
                    "limited": True, "max_input_chars": 12000, "max_output_tokens": 2048}]
