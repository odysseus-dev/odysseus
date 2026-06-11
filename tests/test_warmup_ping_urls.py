"""Startup warmup must resolve real endpoint URLs.

The warmup/keepalive loop called `model_discovery.get_endpoints()`, which does
not exist on ModelDiscovery, so it raised AttributeError every run and pinged
nothing. `ModelDiscovery.warmup_ping_urls()` resolves the /models probe URLs
from the real discovery API.
"""
from src import model_discovery
from src.model_discovery import ModelDiscovery


def _md():
    return ModelDiscovery.__new__(ModelDiscovery)


def test_old_method_never_existed():
    # Documents why the old warmup was a silent no-op.
    assert not hasattr(ModelDiscovery, "get_endpoints")


def test_resolves_models_urls_from_discovered_items():
    md = _md()
    md.discover_models = lambda: {"items": [
        {"url": "http://host:8000/v1/chat/completions", "models": ["a"]},
        {"url": "http://host:1234/v1/chat/completions", "models": ["b"]},
    ]}
    assert md.warmup_ping_urls() == [
        "http://host:8000/v1/models",
        "http://host:1234/v1/models",
    ]


def test_limit_caps_results():
    md = _md()
    md.discover_models = lambda: {"items": [
        {"url": f"http://h:{8000 + i}/v1/chat/completions"} for i in range(10)
    ]}
    assert len(md.warmup_ping_urls(limit=3)) == 3


def test_reuses_cached_warmup_urls_between_keepalive_ticks(monkeypatch):
    md = _md()
    calls = 0

    def fake_discover_models():
        nonlocal calls
        calls += 1
        return {"items": [
            {"url": "http://host:8000/v1/chat/completions", "models": ["a"]},
        ]}

    md.discover_models = fake_discover_models
    monkeypatch.setattr(model_discovery.time, "time", lambda: 1000.0)
    assert md.warmup_ping_urls() == ["http://host:8000/v1/models"]

    monkeypatch.setattr(model_discovery.time, "time", lambda: 1060.0)
    assert md.warmup_ping_urls() == ["http://host:8000/v1/models"]
    assert calls == 1


def test_empty_warmup_result_is_cached(monkeypatch):
    md = _md()
    calls = 0

    def fake_discover_models():
        nonlocal calls
        calls += 1
        return {"items": []}

    md.discover_models = fake_discover_models
    monkeypatch.setattr(model_discovery.time, "time", lambda: 1000.0)
    assert md.warmup_ping_urls() == []

    monkeypatch.setattr(model_discovery.time, "time", lambda: 1060.0)
    assert md.warmup_ping_urls() == []
    assert calls == 1


def test_warmup_url_cache_refreshes_after_ttl(monkeypatch):
    md = _md()
    now = 1000.0
    calls = 0

    def fake_discover_models():
        nonlocal calls
        calls += 1
        port = 8000 + calls
        return {"items": [
            {"url": f"http://host:{port}/v1/chat/completions", "models": ["a"]},
        ]}

    md.discover_models = fake_discover_models
    monkeypatch.setattr(model_discovery.time, "time", lambda: now)
    assert md.warmup_ping_urls() == ["http://host:8001/v1/models"]

    now += model_discovery._WARMUP_URLS_CACHE_TTL + 1
    assert md.warmup_ping_urls() == ["http://host:8002/v1/models"]
    assert calls == 2


def test_discovery_failure_degrades_to_empty():
    md = _md()

    def boom():
        raise RuntimeError("port scan failed")

    md.discover_models = boom
    assert md.warmup_ping_urls() == []
