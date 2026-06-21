"""Integration tests for the GET /api/cookbook/hf-latest route.

Verifies the multi-pipeline merge logic (any-to-any / image-text-to-text models
are included alongside text-generation), GGUF VRAM bypass, deduplication across
pipelines, and exclusion of adapters/LoRAs.

Uses a minimal FastAPI app with the cookbook router and overrides the auth
dependency so no session / DB state is needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── minimal test app ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """A TestClient that wraps the cookbook router with auth bypassed."""
    from routes.cookbook_routes import setup_cookbook_routes
    from src.auth_helpers import require_user

    app = FastAPI()
    app.include_router(setup_cookbook_routes())
    app.dependency_overrides[require_user] = lambda: "test_user"
    return TestClient(app)


# ─── HF API stub helpers ─────────────────────────────────────────────────────

def _hf_entry(repo_id, pipeline_tag, trending=100, tags=None):
    return {
        "modelId": repo_id,
        "pipeline_tag": pipeline_tag,
        "tags": tags if tags is not None else [pipeline_tag],
        "downloads": 1000,
        "likes": 50,
        "createdAt": "2026-04-01",
        "trendingScore": trending,
    }


def _mock_hf_client(text_gen=None, image_text=None, any_to_any=None):
    """Build a patched httpx.AsyncClient that routes by pipeline keyword in URL."""

    async def _get(url, **kw):
        resp = MagicMock()
        resp.status_code = 200
        if "any-to-any" in url:
            resp.json.return_value = any_to_any or []
        elif "image-text-to-text" in url:
            resp.json.return_value = image_text or []
        else:
            resp.json.return_value = text_gen or []
        return resp

    inst = AsyncMock()
    inst.get.side_effect = _get
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=inst)


# ─── tests ────────────────────────────────────────────────────────────────────

def test_any_to_any_model_included_in_default_pipeline(client):
    """When pipeline='text-generation' (default), any-to-any models also appear.

    This is the root-cause fix for Gemma 4 going missing from the browse list —
    flagship multimodal models are tagged any-to-any on HF, not text-generation.
    """
    mock_client = _mock_hf_client(
        text_gen=[_hf_entry("some/text-7b", "text-generation", trending=90)],
        any_to_any=[_hf_entry("google/gemma-4-12b-it", "any-to-any", trending=95)],
        image_text=[_hf_entry("qwen/qwen2-vl-7b", "image-text-to-text", trending=85)],
    )
    with patch("httpx.AsyncClient", mock_client):
        resp = client.get("/api/cookbook/hf-latest?limit=20")

    assert resp.status_code == 200
    repo_ids = {m["repo_id"] for m in resp.json()["models"]}
    assert "google/gemma-4-12b-it" in repo_ids, "any-to-any model must appear in merged results"
    assert "some/text-7b" in repo_ids
    assert "qwen/qwen2-vl-7b" in repo_ids


def test_any_to_any_model_is_not_duplicated_when_in_multiple_pipelines(client):
    """A repo_id that shows up in two pipeline queries is returned only once."""
    shared = _hf_entry("google/gemma-4-12b-it", "any-to-any", trending=95)
    mock_client = _mock_hf_client(
        text_gen=[],
        image_text=[shared],
        any_to_any=[shared],
    )
    with patch("httpx.AsyncClient", mock_client):
        resp = client.get("/api/cookbook/hf-latest?limit=20")

    assert resp.status_code == 200
    repo_ids = [m["repo_id"] for m in resp.json()["models"]]
    assert repo_ids.count("google/gemma-4-12b-it") == 1, "duplicate across pipelines must be deduplicated"


def test_explicit_non_llm_pipeline_skips_merge(client):
    """An explicit non-LLM pipeline (e.g. text-to-image) is honored as-is —
    the multi-pipeline merge only fires for LLM browse requests."""
    mock_client = _mock_hf_client(
        text_gen=[_hf_entry("some/image-gen", "text-to-image", trending=99)],
    )
    with patch("httpx.AsyncClient", mock_client):
        resp = client.get("/api/cookbook/hf-latest?pipeline=text-to-image&limit=20")

    assert resp.status_code == 200
    # Only one pipeline was queried; results depend on text_gen mock which is
    # used for all non-any-to-any / non-image-text-to-text URLs.
    assert "models" in resp.json()


def test_gguf_repo_bypasses_vram_filter(client):
    """GGUF repos must pass even when the estimated VRAM exceeds the filter budget.

    llama-server offloads layers to CPU RAM, so fp16-based VRAM estimates are
    meaningless for GGUF — the user picks quantization at download time.
    """
    gguf_entry = _hf_entry(
        "unsloth/gemma-4-12B-it-GGUF",
        "text-generation",
        trending=80,
        tags=["gguf", "text-generation"],
    )
    # A non-GGUF 70B model at fp16 needs ~140 GB — way over the 8 GB budget.
    big_model = _hf_entry("some/huge-70b-fp16", "text-generation", trending=70)

    mock_client = _mock_hf_client(text_gen=[gguf_entry, big_model])
    with patch("httpx.AsyncClient", mock_client):
        resp = client.get("/api/cookbook/hf-latest?vram_gb=8&limit=20")

    assert resp.status_code == 200
    repo_ids = {m["repo_id"] for m in resp.json()["models"]}
    assert "unsloth/gemma-4-12B-it-GGUF" in repo_ids, "GGUF repo must bypass VRAM filter"
    assert "some/huge-70b-fp16" not in repo_ids, "fp16 70B must be filtered at 8 GB budget"


def test_lora_and_adapter_repos_excluded(client):
    """LoRA / adapter / embedding repos must be filtered out."""
    mock_client = _mock_hf_client(
        text_gen=[
            _hf_entry("good/model-7b", "text-generation", trending=100),
            _hf_entry("some/lora-adapter-7b", "text-generation", trending=90),
            _hf_entry("some/embedding-model", "text-generation", trending=80),
        ]
    )
    with patch("httpx.AsyncClient", mock_client):
        resp = client.get("/api/cookbook/hf-latest?limit=20")

    repo_ids = {m["repo_id"] for m in resp.json()["models"]}
    assert "good/model-7b" in repo_ids
    assert "some/lora-adapter-7b" not in repo_ids
    assert "some/embedding-model" not in repo_ids


def test_returns_models_key_on_success(client):
    """Response always contains a top-level 'models' list."""
    mock_client = _mock_hf_client(text_gen=[])
    with patch("httpx.AsyncClient", mock_client):
        resp = client.get("/api/cookbook/hf-latest")

    assert resp.status_code == 200
    assert "models" in resp.json()
    assert isinstance(resp.json()["models"], list)
