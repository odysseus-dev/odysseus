import asyncio

import services.search.service as svc_mod
from services.search.service import SearchService


def test_search_skips_non_dict_results(monkeypatch):
    # comprehensive_web_search aggregates external provider + cache results;
    # a malformed row (string/None) made the old loop call r.get and crash,
    # losing the whole search.
    def fake_search(query, max_results=10, fetch_content=False):
        return [
            {"url": "https://a.com", "title": "A"},
            "junk-row",
            None,
            {"url": "https://b.com", "title": "B"},
        ]
