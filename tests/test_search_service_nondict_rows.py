import asyncio

import services.search.service as svc_mod
from services.search.service import SearchService


def test_search_skips_non_dict_results(monkeypatch):
    # comprehensive_web_search aggregates external provider + cache results;
    # a malformed row (string/None) made the old loop call r.get and crash,
    # losing the whole search.
    async def fake_search(query, max_results=10, fetch_content=False):
        return [
            {"url": "https://a.com", "title": "A", "snippet": "x"},
            "junk-row",
            None,
            {"url": "https://b.com", "title": "B", "snippet": "y"},
        ]

    monkeypatch.setattr(svc_mod, "comprehensive_web_search", fake_search)
    svc = SearchService()
    res = asyncio.run(svc.search("anything"))
    assert [r.url for r in res.results] == ["https://a.com", "https://b.com"]
    assert res.total == 2
