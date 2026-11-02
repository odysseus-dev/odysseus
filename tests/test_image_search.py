"""Tests for SearXNG-backed image search."""

import asyncio

from services.search import images as image_module
from routes.search import search_routes


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_image_search_normalizes_searxng_results(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse({"results": [
            {
                "title": "Red panda",
                "img_src": "https://example.com/panda.jpg",
                "thumbnail_src": "https://thumb.example.com/panda.jpg",
                "url": "https://example.com/red-panda",
                "resolution": "1200 x 800",
                "img_format": "image/jpeg",
            },
            {
                "title": "Second",
                "img_src": "//cdn.example.com/second.jpg",
                "url": "https://source.example.com/second",
                "resolution": "unknown",
            },
            {"title": "Never inline", "img_src": "data:image/png;base64,abc"},
            {"title": "No image"},
        ]})

    monkeypatch.setattr(image_module.httpx, "get", fake_get)
    monkeypatch.setattr(image_module, "_safesearch_for", lambda _provider: "2")

    results = image_module.image_search("red panda", count=10)

    assert len(results) == 2
    assert results[0]["title"] == "Red panda"
    assert results[0]["image"] == "https://example.com/panda.jpg"
    assert results[0]["thumbnail"] == "https://thumb.example.com/panda.jpg"
    assert results[0]["page_url"] == "https://example.com/red-panda"
    assert results[0]["width"] == 1200
    assert results[0]["height"] == 800
    assert results[0]["source"] == "example.com"
    assert results[0]["provider"] == "searxng"
    assert results[1]["image"] == "https://cdn.example.com/second.jpg"
    assert results[1]["thumbnail"] == "https://cdn.example.com/second.jpg"
    assert results[1]["width"] is None
    assert captured["params"]["categories"] == "images"
    assert captured["params"]["language"] == "en"
    assert captured["params"]["safesearch"] == "2"


def test_image_search_respects_limit(monkeypatch):
    payload = {"results": [
        {"title": str(i), "img_src": f"https://example.com/{i}.jpg", "url": "https://example.com"}
        for i in range(10)
    ]}
    monkeypatch.setattr(image_module.httpx, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(image_module, "_safesearch_for", lambda _provider: "2")

    assert len(image_module.image_search("x", count=3)) == 3


def test_image_search_empty_query_makes_no_request(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("should not call SearXNG for an empty query")

    monkeypatch.setattr(image_module.httpx, "get", fail)
    assert image_module.image_search("   ") == []


def test_image_search_fails_open(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("SearXNG down")

    monkeypatch.setattr(image_module.httpx, "get", fail)
    assert image_module.image_search("red panda") == []


def test_image_routes_are_registered_and_render():
    router = search_routes.setup_search_routes(None)
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/search/images" in paths
    assert "/api/search/images" in paths

    page_route = next(route for route in router.routes if getattr(route, "path", "") == "/search/images")
    response = asyncio.run(page_route.endpoint(q="red panda"))
    page = response.body.decode("utf-8")
    assert "Image Search" in page
    assert "red panda" in page
    assert "/search/web?q=red%20panda" in page
    assert "__Q_JSON__" not in page
    assert "__Q_HREF__" not in page


def test_image_api_route_returns_rows(monkeypatch):
    monkeypatch.setattr(search_routes, "image_search", lambda query, count=24: [
        {"title": "One", "image": "https://example.com/1.jpg"}
        for _ in range(min(count, 2))
    ])
    router = search_routes.setup_search_routes(None)
    api_route = next(route for route in router.routes if getattr(route, "path", "") == "/api/search/images")
    payload = asyncio.run(api_route.endpoint(q="x", count=2))
    assert payload["count"] == 2
    assert payload["provider"] == "searxng"
    assert [row["title"] for row in payload["images"]] == ["One", "One"]
