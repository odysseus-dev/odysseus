"""DR-1 unit tests for SearXNG pre-flight health check and fail-fast behavior."""

from __future__ import annotations

import httpx
import pytest

from src.research_handler import ResearchHandler


@pytest.mark.asyncio
async def test_probe_search_provider_succeeds_when_searxng_healthy(monkeypatch):
    class _HealthyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url: str):
            return httpx.Response(200, text="OK", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", _HealthyClient)
    await ResearchHandler._probe_search_provider("searxng")


@pytest.mark.asyncio
async def test_probe_search_provider_fails_fast_when_searxng_down(monkeypatch):
    class _OfflineClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url: str):
            raise httpx.ConnectError("Connection refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", _OfflineClient)
    with pytest.raises(RuntimeError, match="Cannot reach SearXNG search engine"):
        await ResearchHandler._probe_search_provider("searxng")


@pytest.mark.asyncio
async def test_call_research_service_fails_fast_before_researcher_when_searxng_down(monkeypatch):
    handler = ResearchHandler()

    async def _ok_llm(*args, **kwargs):
        return None

    async def _down_search(*args, **kwargs):
        raise RuntimeError("Cannot reach SearXNG search engine at http://127.0.0.1:8080")

    monkeypatch.setattr(handler, "_probe_endpoint", _ok_llm)
    monkeypatch.setattr(handler, "_probe_search_provider", _down_search)

    with pytest.raises(RuntimeError, match="Cannot reach SearXNG search engine at http://127.0.0.1:8080"):
        await handler.call_research_service(
            query="Test DR-1 preflight",
            llm_endpoint="http://127.0.0.1:11434/v1",
            llm_model="gemma4:31b",
        )
