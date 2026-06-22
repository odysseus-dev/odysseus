import json

import pytest

from src import tool_implementations as tools


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        return {"session_id": "rp-test"}


@pytest.mark.asyncio
async def test_trigger_research_forbidden_tells_agent_not_to_retry(monkeypatch):
    import httpx

    posts = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posts.append((url, json, headers))
            return _FakeResponse(status_code=403, text='{"detail":"Forbidden"}')

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await tools.do_trigger_research(
        json.dumps({"topic": "https://example.com research this"}),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert "Do not retry trigger_research" in result["error"]
    assert "use web_fetch" in result["error"]
    assert posts[0][2]["X-Odysseus-Owner"] == "admin"
