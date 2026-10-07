"""Read-only, reproducible provider probe. Prints JSON; never changes settings.

Run with the application's Python from the repository root. Queries are public
regressions plus unrelated controls. Coverage is diagnostic, not an accuracy score.
"""
import concurrent.futures
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from services.search.providers import _get_search_instance, _safesearch_for

QUERIES = [
    "What country has best meat",
    "Sweden 78 year old British woman deportation Brexit residence application",
    "Latest news in AI",
    "Any latest info on quantum physics",
    "What year did Ethiopia become independent",
    "PostgreSQL transaction isolation documentation",
    "Kyoto weather tomorrow",
]
ENGINES = ["bing", "mojeek", "presearch", "duckduckgo", "google", "bing news", "yep"]


def probe(pair):
    query, engine = pair
    start = time.monotonic()
    try:
        response = httpx.get(
            _get_search_instance() + "/search",
            params={"q": query, "engines": engine, "format": "json",
                    "language": "en", "safesearch": _safesearch_for("searxng")},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        return {"query": query, "engine": engine,
                "seconds": round(time.monotonic() - start, 2),
                "unresponsive": data.get("unresponsive_engines", []),
                "results": [{k: row.get(k) for k in (
                    "title", "url", "content", "engines", "publishedDate"
                )} for row in data.get("results", [])[:5]]}
    except Exception as exc:
        return {"query": query, "engine": engine, "error": type(exc).__name__}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(probe, [(q, e) for q in QUERIES for e in ENGINES]))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
