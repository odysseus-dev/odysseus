"""Tests for the dynamic hwfit catalog persistence layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.database import DiscoveredModel, SessionLocal
from services.hwfit.catalog_sync import (
    discovered_model_to_dict,
    fetch_hf_trending_catalog,
    get_discovered_catalog,
    refresh_hf_trending_catalog,
    seed_static_catalog,
    upsert_discovered_model,
)
from services.hwfit.fit import rank_models


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


@pytest.fixture
def db():
    session = SessionLocal()
    session.query(DiscoveredModel).delete()
    session.commit()
    try:
        yield session
    finally:
        session.query(DiscoveredModel).delete()
        session.commit()
        session.close()


def test_upsert_discovered_model_creates_row(db):
    row = upsert_discovered_model(
        db,
        {
            "name": "google/gemma-test",
            "provider": "Google",
            "parameter_count": "4B",
            "quantization": "Q4_K_M",
            "pipeline_tag": "text-generation",
            "context_length": 8192,
            "gguf_sources": [{"repo": "example/gemma-test-GGUF"}],
        },
        source="hf_trending",
    )
    db.commit()

    assert row.id
    assert row.name == "google/gemma-test"
    assert row.source == "hf_trending"
    assert row.params_b == 4.0
    assert row.gguf_sources == [{"repo": "example/gemma-test-GGUF"}]


def test_upsert_discovered_model_updates_existing_equal_priority(db):
    upsert_discovered_model(
        db,
        {"name": "google/gemma-test", "provider": "Google", "parameter_count": "4B"},
        source="hf_trending",
    )
    row = upsert_discovered_model(
        db,
        {"name": "google/gemma-test", "provider": "Google", "parameter_count": "12B"},
        source="hf_trending",
    )
    db.commit()

    assert row.parameter_count == "12B"
    assert row.params_b == 12.0


def test_source_priority_local_overwrites_hf_for_same_name(db):
    upsert_discovered_model(
        db,
        {
            "name": "local/Phi-3-mini-4B-Q4_K_M",
            "provider": "Hugging Face",
            "parameter_count": "4B",
            "gguf_sources": [{"repo": "hf/phi"}],
        },
        source="hf_trending",
    )
    row = upsert_discovered_model(
        db,
        {
            "name": "local/Phi-3-mini-4B-Q4_K_M",
            "provider": "Local GGUF",
            "parameter_count": "4B",
            "local_path": "D:/Models/Phi-3-mini-4B-Q4_K_M.gguf",
            "gguf_sources": [{"path": "D:/Models/Phi-3-mini-4B-Q4_K_M.gguf"}],
        },
        source="local_gguf",
    )
    db.commit()

    model = discovered_model_to_dict(row)
    assert model["_source"] == "local_gguf"
    assert model["provider"] == "Local GGUF"
    assert model["local_path"] == "D:/Models/Phi-3-mini-4B-Q4_K_M.gguf"
    assert model["gguf_sources"] == [{"path": "D:/Models/Phi-3-mini-4B-Q4_K_M.gguf"}]


def test_source_priority_does_not_demote_local_with_hf_refresh(db):
    upsert_discovered_model(
        db,
        {
            "name": "local/Phi-3-mini-4B-Q4_K_M",
            "provider": "Local GGUF",
            "parameter_count": "4B",
            "local_path": "D:/Models/Phi-3-mini-4B-Q4_K_M.gguf",
        },
        source="local_gguf",
    )
    row = upsert_discovered_model(
        db,
        {
            "name": "local/Phi-3-mini-4B-Q4_K_M",
            "provider": "Hugging Face",
            "parameter_count": "4B",
            "release_date": "2026-06-01",
        },
        source="hf_trending",
    )
    db.commit()

    assert row.source == "local_gguf"
    assert row.provider == "Local GGUF"
    assert row.release_date == "2026-06-01"


def test_source_priority_orders_all_catalog_sources(db):
    name = "qwen/test-7b-q4"
    for source in ("hf_trending", "ollama", "lmstudio", "local_gguf"):
        row = upsert_discovered_model(
            db,
            {
                "name": name,
                "provider": source,
                "parameter_count": "7B",
                "backend": source,
            },
            source=source,
        )

    assert row.source == "local_gguf"
    assert row.provider == "local_gguf"


def test_seed_static_catalog_only_when_empty(db):
    first_count = seed_static_catalog(db)
    second_count = seed_static_catalog(db)

    assert first_count > 0
    assert second_count == 0
    assert db.query(DiscoveredModel).count() == first_count


def test_rank_models_accepts_db_catalog_override(db):
    upsert_discovered_model(
        db,
        {
            "name": "local/Phi-3-mini-4B-Q4_K_M",
            "provider": "Local GGUF",
            "parameter_count": "4B",
            "quantization": "Q4_K_M",
            "context_length": 4096,
            "gguf_sources": [{"path": "D:/Models/Phi-3-mini-4B-Q4_K_M.gguf"}],
        },
        source="local_gguf",
    )
    catalog = get_discovered_catalog(db, seed_if_empty=False)

    results = rank_models(
        _cpu_system(),
        search="Phi-3-mini",
        catalog_models=catalog,
        limit=5,
    )

    match = next(r for r in results if r["name"] == "local/Phi-3-mini-4B-Q4_K_M")
    assert match["_source"] == "local_gguf"
    assert match["source"] == "local_gguf"


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
        response = MagicMock()
        response.status_code = 200
        if "any-to-any" in url:
            response.json.return_value = any_to_any or []
        elif "image-text-to-text" in url:
            response.json.return_value = image_text or []
        else:
            response.json.return_value = text_gen or []
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=client)


@pytest.mark.asyncio
async def test_fetch_hf_trending_catalog_merges_and_filters_pipelines():
    mock_client = _mock_hf_client(
        text_gen=[
            _hf_entry("good/model-7b", "text-generation", trending=80),
            _hf_entry("bad/lora-adapter-7b", "text-generation", trending=90),
        ],
        image_text=[_hf_entry("qwen/qwen2-vl-7b", "image-text-to-text", trending=70)],
        any_to_any=[_hf_entry("google/gemma-4-12b-it", "any-to-any", trending=100)],
    )

    with patch("httpx.AsyncClient", mock_client):
        entries = await fetch_hf_trending_catalog(limit=10)

    names = [entry["name"] for entry in entries]
    assert names[:3] == ["google/gemma-4-12b-it", "good/model-7b", "qwen/qwen2-vl-7b"]
    assert "bad/lora-adapter-7b" not in names
    gemma = entries[0]
    assert gemma["source"] == "hf_trending"
    assert gemma["parameter_count"] == "12B"
    assert gemma["capabilities"] == ["vision"]


@pytest.mark.asyncio
async def test_refresh_hf_trending_catalog_upserts_rows(db):
    mock_client = _mock_hf_client(
        text_gen=[_hf_entry("unsloth/gemma-4-12B-it-GGUF", "text-generation", tags=["gguf"])]
    )

    with patch("httpx.AsyncClient", mock_client):
        count = await refresh_hf_trending_catalog(limit=10)

    assert count == 1
    row = db.query(DiscoveredModel).filter(DiscoveredModel.name == "unsloth/gemma-4-12B-it-GGUF").first()
    assert row is not None
    model = discovered_model_to_dict(row)
    assert model["source"] == "hf_trending"
    assert model["is_gguf"] is True
    assert model["gguf_sources"] == [{"repo": "unsloth/gemma-4-12B-it-GGUF", "kind": "GGUF"}]
