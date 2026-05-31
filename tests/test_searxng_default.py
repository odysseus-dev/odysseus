"""Pin the SearXNG env fallback to the bundled Compose port (8080)."""

import importlib


def test_searxng_instance_default_without_env(monkeypatch):
    """Runtime search uses src.constants.SEARXNG_INSTANCE when settings.search_url is empty."""
    monkeypatch.delenv("SEARXNG_INSTANCE", raising=False)
    import src.constants

    importlib.reload(src.constants)
    assert src.constants.SEARXNG_INSTANCE == "http://localhost:8080"
