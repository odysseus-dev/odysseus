"""DuckDuckGo search must import ddgs, not deprecated duckduckgo_search."""
from services.search import providers


class _FakeDDGS:
    def __init__(self):
        self.calls = []

    def text(self, query, max_results=None, timelimit=None, safesearch=None):
        self.calls.append({"query": query, "max_results": max_results})
        return [{"title": "T", "href": "https://example.com/a", "body": "snippet"}]


def test_duckduckgo_search_uses_ddgs_module(monkeypatch):
    fake = _FakeDDGS()
    monkeypatch.delitem(__import__("sys").modules, "duckduckgo_search", raising=False)

    class DDGS:
        def __init__(self):
            self._fake = fake

        def text(self, *a, **kw):
            return self._fake.text(*a, **kw)

    ddgs_mod = type(__import__("sys"))("ddgs")
    ddgs_mod.DDGS = DDGS
    monkeypatch.setitem(__import__("sys").modules, "ddgs", ddgs_mod)
    monkeypatch.setattr(providers, "_get_result_count", lambda: 3)
    monkeypatch.setattr(providers, "_safesearch_for", lambda _k: "moderate")

    results = providers.duckduckgo_search("hercules journeys", count=1)

    assert results[0]["url"] == "https://example.com/a"
    assert fake.calls[0]["query"] == "hercules journeys"
