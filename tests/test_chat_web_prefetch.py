from src.chat_processor import (
    ChatProcessor,
    normalize_web_search_query,
    should_prefetch_web_search,
)


class _MemoryManager:
    def load(self, owner=None):
        return []


class _PersonalDocsManager:
    rag_manager = None


def _processor():
    return ChatProcessor(_MemoryManager(), _PersonalDocsManager())


def _build_preface(message, **kwargs):
    return _processor().build_context_preface(
        message,
        session=object(),
        use_web=True,
        use_rag=False,
        use_memory=False,
        **kwargs,
    )


def test_web_prefetch_skips_social_greeting(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return "unexpected context", [{"title": "unexpected"}]

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", fake_search)

    preface, rag_sources, web_sources = _build_preface("Heallooo")

    assert calls == []
    assert rag_sources == []
    assert web_sources == []
    assert not any("web search results" in item["content"] for item in preface)


def test_web_prefetch_normalizes_explicit_search_query(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return "search context", [{"title": "Odysseus release"}]

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", fake_search)

    preface, _, web_sources = _build_preface("Please search the web for latest Odysseus release")

    assert calls == [("latest Odysseus release", {"time_filter": None, "return_sources": True})]
    assert web_sources == [{"title": "Odysseus release"}]
    assert any("search context" in item["content"] for item in preface)


def test_web_prefetch_allows_current_information_questions(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return "weather context", [{"title": "Toronto weather"}]

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", fake_search)

    _build_preface("What is the weather in Toronto today?")

    assert calls == [
        ("What is the weather in Toronto today", {"time_filter": None, "return_sources": True})
    ]


def test_web_prefetch_preserves_explicit_toggle_for_informational_queries(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return "regulation context", [{"title": "EU AI regulation"}]

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", fake_search)

    _build_preface("Explain the new EU AI regulation")

    assert calls == [
        ("Explain the new EU AI regulation", {"time_filter": None, "return_sources": True})
    ]


def test_web_prefetch_helpers_preserve_explicit_toggle_intent():
    assert not should_prefetch_web_search("Heallooo")
    assert not should_prefetch_web_search("thanks!")
    assert should_prefetch_web_search("Can you explain recursion?")
    assert should_prefetch_web_search("what is X")
    assert should_prefetch_web_search("why does X happen?")
    assert should_prefetch_web_search("how do I use X?")
    assert should_prefetch_web_search("Who is the current CEO of OpenAI?")
    assert should_prefetch_web_search("find sources for small language model benchmarks")
    assert normalize_web_search_query("Can you look up Python 3.14 release date?") == "Python 3.14 release date"
    assert normalize_web_search_query("Google stock price today") == "Google stock price today"
