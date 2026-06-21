from unittest.mock import MagicMock

import pytest

from services.hwfit.fit import rank_models
from services.hwfit.lmstudio_catalog import (
    _candidate_urls,
    fetch_lmstudio_models,
    invalidate_cache,
)


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


def _cpu_system():
    return {
        "has_gpu": False,
        "backend": "cpu_x86",
        "gpu_name": None,
        "gpu_vram_gb": 0,
        "gpu_count": 0,
        "available_ram_gb": 32.0,
        "total_ram_gb": 32.0,
    }


@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def test_candidate_urls_include_lm_studio_env(monkeypatch):
    monkeypatch.setenv("LM_STUDIO_URL", "http://my-lm-box:5000/v1")

    urls = _candidate_urls("")

    assert urls[0] == "http://my-lm-box:5000/api/v1/models"


def test_candidate_urls_use_remote_host_when_supplied(monkeypatch):
    monkeypatch.setenv("LM_STUDIO_URL", "http://ignored:5000")

    assert _candidate_urls("workstation.local") == [
        "http://workstation.local:1234/api/v1/models"
    ]


def test_fetch_lmstudio_models_parses_native_models(monkeypatch):
    payload = {
        "models": [
            {
                "type": "llm",
                "key": "qwen3.6-27b-Q5_K_M",
                "display_name": "Qwen3.6 27B",
                "architecture": "qwen3",
                "quantization": {"name": "Q5_K_M"},
                "format": "gguf",
                "max_context_length": 131072,
                "capabilities": {"vision": True, "tools": True},
            }
        ]
    }

    monkeypatch.setattr(
        "services.hwfit.lmstudio_catalog.httpx.get",
        lambda url, timeout=None: _FakeResponse(payload),
    )

    result = fetch_lmstudio_models()

    assert len(result) == 1
    model = result[0]
    assert model["name"] == "Qwen3.6 27B"
    assert model["provider"] == "LM Studio"
    assert model["parameter_count"] == "27B"
    assert model["quant"] == "Q5_K_M"
    assert model["context_length"] == 131072
    assert model["backend"] == "lmstudio"
    assert model["_source"] == "lmstudio"
    assert model["is_gguf"] is True
    assert model["gguf_sources"]
    assert model["capabilities"] == ["vision", "tools"]


def test_fetch_lmstudio_models_returns_empty_for_non_lmstudio(monkeypatch):
    monkeypatch.setattr(
        "services.hwfit.lmstudio_catalog.httpx.get",
        lambda url, timeout=None: _FakeResponse({"data": [{"id": "gpt-4o"}]}),
    )

    assert fetch_lmstudio_models() == []


def test_fetch_lmstudio_models_uses_cache(monkeypatch):
    payload = {
        "models": [
            {
                "key": "phi-4-mini-4B-Q4_K_M",
                "architecture": "phi",
                "quantization": {"name": "Q4_K_M"},
                "format": "gguf",
            }
        ]
    }
    get = MagicMock(return_value=_FakeResponse(payload))
    monkeypatch.setattr("services.hwfit.lmstudio_catalog.httpx.get", get)

    fetch_lmstudio_models()
    fetch_lmstudio_models()

    assert get.call_count == 1


def test_rank_models_preserves_lmstudio_source(monkeypatch):
    payload = {
        "models": [
            {
                "key": "phi-4-mini-4B-Q4_K_M",
                "architecture": "phi",
                "quantization": {"name": "Q4_K_M"},
                "format": "gguf",
            }
        ]
    }
    monkeypatch.setattr(
        "services.hwfit.lmstudio_catalog.httpx.get",
        lambda url, timeout=None: _FakeResponse(payload),
    )
    [model] = fetch_lmstudio_models()

    results = rank_models(_cpu_system(), search="phi-4-mini", extra_models=[model])

    match = next(r for r in results if r["name"] == "phi-4-mini-4B-Q4_K_M")
    assert match["_source"] == "lmstudio"
    assert match["source"] == "lmstudio"
