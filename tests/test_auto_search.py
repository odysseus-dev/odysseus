"""Experimental auto-search gate.

When enabled, a fast utility-LLM yes/no decides whether a message needs the web
before searching. Must be conservative: never search on uncertainty/failure,
never override a user who already enabled (or didn't enable) search by hand.
"""

import asyncio
import services.search.query as q


def _run(coro):
    return asyncio.run(coro)


def _patch_llm(monkeypatch, reply, *, resolve=("http://x/v1", "m", {})):
    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint",
                        lambda *a, **k: resolve, raising=False)

    async def fake_call(url, model, messages, **kw):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_call, raising=False)


def test_should_search_yes(monkeypatch):
    _patch_llm(monkeypatch, "YES")
    assert _run(q.should_search("what's the latest news on the election?")) is True


def test_should_search_no(monkeypatch):
    _patch_llm(monkeypatch, "NO")
    assert _run(q.should_search("write me a poem about the sea")) is False


def test_should_search_tolerates_chatty_yes(monkeypatch):
    _patch_llm(monkeypatch, "Yes, that needs current info.")
    assert _run(q.should_search("is X still alive?")) is True


def test_should_search_false_on_error(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("down"))
    assert _run(q.should_search("anything")) is False


def test_should_search_false_without_utility_model(monkeypatch):
    _patch_llm(monkeypatch, "YES", resolve=(None, None, None))
    assert _run(q.should_search("anything")) is False


def test_should_search_false_on_empty_or_huge(monkeypatch):
    _patch_llm(monkeypatch, "YES")
    assert _run(q.should_search("")) is False
    assert _run(q.should_search("x" * 3000)) is False
