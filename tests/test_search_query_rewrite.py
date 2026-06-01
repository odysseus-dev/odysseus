"""LLM-assisted search query rewrite.

A conversational question makes a poor search query; rewrite_search_query asks
the utility LLM to reformulate it into keywords. Critically, it must NEVER make
search worse than today — on any failure (no LLM, timeout, junk output) it
returns the original query unchanged.
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


def test_rewrite_reformulates_question(monkeypatch):
    _patch_llm(monkeypatch, "Humberto Maturana death date")
    out = _run(q.rewrite_search_query("is humberto maturana dead?"))
    assert out == "Humberto Maturana death date"


def test_rewrite_strips_quotes_and_extra_lines(monkeypatch):
    _patch_llm(monkeypatch, '"Python asyncio tutorial"\n(here is why)')
    out = _run(q.rewrite_search_query("how do I use asyncio in python?"))
    assert out == "Python asyncio tutorial"


def test_rewrite_falls_back_on_llm_error(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("endpoint down"))
    original = "is humberto maturana dead?"
    assert _run(q.rewrite_search_query(original)) == original


def test_rewrite_falls_back_when_no_utility_model(monkeypatch):
    _patch_llm(monkeypatch, "x", resolve=(None, None, None))
    original = "what is the capital of france?"
    assert _run(q.rewrite_search_query(original)) == original


def test_rewrite_rejects_overlong_output(monkeypatch):
    # Model explained instead of rewriting → far longer than the question → reject.
    _patch_llm(monkeypatch, "Sure! " + "word " * 80)
    original = "is X dead?"
    assert _run(q.rewrite_search_query(original)) == original


def test_rewrite_passthrough_empty(monkeypatch):
    _patch_llm(monkeypatch, "anything")
    assert _run(q.rewrite_search_query("")) == ""
