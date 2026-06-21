"""Auto web search (use_web toggle) must search a focused query, not the raw
conversational message.

Regression for the bug where "Chat, pesquisa o resultado da moto GP de hoje e
traga um resumo" was sent verbatim to the search engine, which then ranked on
the greeting word "chat" and returned chat-room / ChatGPT pages instead of
MotoGP results. The fix condenses the message to a search query via a short LLM
call (src.chat_processor._search_query_from_message) before searching.
"""

import types

import pytest

from src.chat_processor import (
    ChatProcessor,
    _clean_search_query,
    _search_query_from_message,
)


def _session(endpoint_url="http://local/v1/chat/completions", model="m", headers=None):
    return types.SimpleNamespace(
        endpoint_url=endpoint_url, model=model, headers=headers or {}
    )


# --- _clean_search_query: reduce an LLM response to one clean keyword line ---

def test_clean_search_query_strips_wrapping_quotes():
    assert _clean_search_query('"resultado MotoGP hoje"') == "resultado MotoGP hoje"


def test_clean_search_query_strips_query_prefix():
    assert _clean_search_query("Query: resultado MotoGP hoje") == "resultado MotoGP hoje"


def test_clean_search_query_strips_think_block():
    raw = "<think>user wants today's MotoGP result</think>\nresultado MotoGP hoje"
    assert _clean_search_query(raw) == "resultado MotoGP hoje"


def test_clean_search_query_collapses_to_first_nonempty_line():
    assert _clean_search_query("\n\nresultado MotoGP hoje\nextra noise") == "resultado MotoGP hoje"


def test_clean_search_query_empty_input_returns_empty():
    assert _clean_search_query("") == ""
    assert _clean_search_query("   ") == ""


# --- _search_query_from_message: LLM condensation with safe fallbacks ---

def test_returns_raw_message_when_no_endpoint():
    calls = []

    def boom(*a, **k):  # llm_call must NOT be invoked without an endpoint
        calls.append(1)
        raise AssertionError("llm_call should not run without endpoint/model")

    import src.llm_core as llm_core
    orig = llm_core.llm_call
    llm_core.llm_call = boom
    try:
        out = _search_query_from_message("MotoGP hoje", url="", model="")
    finally:
        llm_core.llm_call = orig
    assert out == "MotoGP hoje"
    assert calls == []


def test_falls_back_to_raw_message_on_llm_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr("src.llm_core.llm_call", boom)
    msg = "Chat, pesquisa o resultado da moto GP de hoje"
    assert _search_query_from_message(msg, "http://x", "m") == msg


def test_returns_cleaned_generated_query(monkeypatch):
    monkeypatch.setattr(
        "src.llm_core.llm_call",
        lambda *a, **k: '"resultado MotoGP hoje 2026"',
    )
    out = _search_query_from_message(
        "Chat, pesquisa o resultado da moto GP de hoje", "http://x", "m"
    )
    assert out == "resultado MotoGP hoje 2026"


# --- regression: build_context_preface must search the generated query ---

def test_build_context_preface_web_searches_generated_query_not_raw(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured["query"] = query
        return ("WEB RESULTS", [{"url": "https://motogp.com", "title": "MotoGP"}])

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", fake_search)
    monkeypatch.setattr(
        "src.llm_core.llm_call", lambda *a, **k: "resultado MotoGP hoje"
    )

    cp = ChatProcessor(memory_manager=None, personal_docs_manager=None)
    preface, rag_sources, web_sources = cp.build_context_preface(
        message="Chat, pesquisa o resultado da moto GP de hoje e traga um resumo",
        session=_session(),
        use_web=True,
        use_rag=False,
        use_memory=False,
    )

    assert captured["query"] == "resultado MotoGP hoje"
    assert "Chat," not in captured["query"]
    assert web_sources and web_sources[0]["url"] == "https://motogp.com"
