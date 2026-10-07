import pytest

from services.hwfit.fit import rank_models
from services.hwfit.models import get_models, is_prequantized
from tests.hwfit_publication_fixtures import publication_catalog  # noqa: F401

# Rank authored inputs rather than publication catalog snapshots.
pytestmark = pytest.mark.usefixtures("publication_catalog")


def _8gb_vram_system():
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA GeForce RTX 4060",
        "gpu_vram_gb": 8.0,
        "gpu_count": 1,
        "available_ram_gb": 32.0,
        "total_ram_gb": 32.0,
    }


GEMMA = "google/gemma-4-12B-it"


def _input_row(rows, name):
    return next(r for r in rows if r["name"] == name)


def _qat_rows(rows):
    return [r for r in rows if r["name"].startswith(GEMMA + "-qat-")]


def test_gemma4_12b_user_catalog_row_wins_the_merge(publication_catalog, monkeypatch):
    """A dynamic cache listing the same repo must not replace or duplicate the
    user-catalog row; rows only the cache knows are still merged in."""
    from services.hwfit import hf_discovery

    monkeypatch.setattr(hf_discovery, "load_cached_hf_collection_models", lambda: [
        {"name": GEMMA, "quantization": "F16", "gguf_sources": [{"repo": "test/cache-GGUF", "file": "cache.gguf"}]},
        {"name": "test/cache-only", "quantization": "F16", "gguf_sources": []},
    ])
    merged = get_models()
    names = [m["name"] for m in merged]

    assert names.count(GEMMA) == 1
    assert "test/cache-only" in names
    entry = next(m for m in merged if m["name"] == GEMMA)
    assert entry["gguf_sources"] == _input_row(publication_catalog, GEMMA)["gguf_sources"]


def test_gemma4_12b_ranked_row_carries_its_gguf_source(publication_catalog):
    hit = next(r for r in rank_models(_8gb_vram_system(), search="gemma-4-12B-it", limit=20) if r["name"] == GEMMA)
    assert hit["gguf_sources"] == _input_row(publication_catalog, GEMMA)["gguf_sources"]
    # The F16 input does not fit 8 GB, so the fit falls back to a GGUF quant.
    assert hit["quant"] == "Q4_K_M"


def test_gemma4_12b_rank_models_returns_it_for_8gb_vram():
    results = rank_models(_8gb_vram_system(), search="gemma-4-12B-it", limit=20)
    names = [r["name"] for r in results]
    assert GEMMA in names, "rank_models did not return gemma-4-12B-it for 8 GB VRAM"


def test_gemma4_12b_qat_entries_rank_with_their_native_quant(publication_catalog):
    qat = _qat_rows(publication_catalog)
    assert len(qat) == 2
    ranked = {r["name"]: r for r in rank_models(_8gb_vram_system(), search="gemma-4-12B-it-qat", limit=20)}
    for row in qat:
        assert ranked[row["name"]]["quant"] == row["quantization"]


def test_gemma4_12b_qat_entries_are_prequantized(publication_catalog):
    catalog = {m["name"]: m for m in get_models()}
    for row in _qat_rows(publication_catalog):
        assert is_prequantized(catalog[row["name"]])


def test_gemma4_12b_qat_entries_have_no_gguf(publication_catalog):
    ranked = {r["name"]: r for r in rank_models(_8gb_vram_system(), search="gemma-4-12B-it-qat", limit=20)}
    for row in _qat_rows(publication_catalog):
        assert ranked[row["name"]]["gguf_sources"] == []
