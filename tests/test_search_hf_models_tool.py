import json

import pytest

from src.tools.cookbook import do_search_hf_models


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    requests = []
    payloads = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return _FakeResponse(self.payloads.pop(0))


@pytest.mark.asyncio
async def test_search_hf_models_uses_official_author_for_latest_qwen(monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.payloads = [[
        {
            "id": "Qwen/Qwen3.8-Flash-Next",
            "author": "Qwen",
            "lastModified": "2026-08-26T12:29:54.000Z",
            "downloads": 2551,
            "likes": 3632,
            "pipeline_tag": "image-text-to-text",
        },
        {
            "id": "community/Qwen3.8-Flash-Next-GGUF",
            "author": "community",
            "lastModified": "2026-08-26T12:30:00.000Z",
            "downloads": 10,
            "likes": 1,
        },
        {
            "id": "Qwen/Qwen3.8-Flash-Next-FP8",
            "author": "Qwen",
            "lastModified": "2026-08-26T11:55:24.000Z",
            "downloads": 451,
            "likes": 94,
        },
    ]]
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    result = await do_search_hf_models(json.dumps({"query": "Qwen latest", "limit": 5}))

    assert result["exit_code"] == 0
    assert result["official_author"] == "Qwen"
    assert result["models"][0]["id"] == "Qwen/Qwen3.8-Flash-Next"
    assert "community/Qwen3.8-Flash-Next-GGUF" not in result["output"]
    assert "Qwen/Qwen3.8-Flash-Next-FP8" not in result["output"]
    url, kwargs = _FakeAsyncClient.requests[0]
    assert url == "https://huggingface.co/api/models"
    assert kwargs["params"]["author"] == "Qwen"
    assert kwargs["params"]["sort"] == "lastModified"


@pytest.mark.asyncio
async def test_search_hf_models_filters_quant_variants_unless_requested(monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.payloads = [[
        {"id": "Qwen/Qwen3-8B", "downloads": 1000, "tags": ["text-generation"]},
        {"id": "unsloth/Qwen3-8B-GGUF", "downloads": 9000, "tags": ["gguf"]},
    ]]
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    result = await do_search_hf_models(json.dumps({"query": "Qwen 8B", "limit": 10}))

    assert [m["id"] for m in result["models"]] == ["Qwen/Qwen3-8B"]

    _FakeAsyncClient.payloads = [[
        {"id": "Qwen/Qwen3-8B", "downloads": 1000, "tags": ["text-generation"]},
        {"id": "unsloth/Qwen3-8B-GGUF", "downloads": 9000, "tags": ["gguf"]},
    ]]

    result = await do_search_hf_models(json.dumps({"query": "Qwen 8B GGUF", "limit": 10}))

    assert [m["id"] for m in result["models"]] == [
        "Qwen/Qwen3-8B",
        "unsloth/Qwen3-8B-GGUF",
    ]
