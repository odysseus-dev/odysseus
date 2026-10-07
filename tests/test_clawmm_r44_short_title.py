from services.search import core


def test_short_distinctive_paper_name_uses_exact_metadata_before_providers(
    monkeypatch,
    tmp_path,
):
    provider_calls = []
    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(core, "search_cache_index", {})
    monkeypatch.setattr(core, "_record_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        core,
        "_get_search_settings",
        lambda: {"search_provider": "searxng", "search_fallback_chain": []},
    )
    monkeypatch.setattr(core, "_build_provider_chain", lambda provider: [provider])
    monkeypatch.setattr(
        core,
        "_call_provider",
        lambda *args, **kwargs: provider_calls.append((args, kwargs)) or [],
    )
    monkeypatch.setattr(
        core,
        "_direct_scholarly_title_results",
        lambda title, count: [{
            "title": "Qwen2-VL: Enhancing Vision-Language Model Perception",
            "url": "https://arxiv.org/abs/2409.12191",
            "snippet": "Official paper.",
            "source": "openalex",
        }] if title == "Qwen2-VL" else [],
    )

    results = core.searxng_search_results(
        "Qwen2-VL paper multimodal benchmarks Table 2 Table 4",
        count=5,
    )
    table_results = core.searxng_search_results(
        "Qwen2-VL Table 2 benchmark scores",
        count=5,
    )

    assert provider_calls == []
    assert results[0]["url"] == "https://arxiv.org/abs/2409.12191"
    assert table_results[0]["url"] == "https://arxiv.org/abs/2409.12191"


def test_generic_short_paper_phrase_does_not_claim_exact_title_resolution():
    assert core._scholarly_title_from_query(
        "recent paper benchmark results"
    ) == ""
    assert core._scholarly_title_from_query(
        "well-known paper benchmark results"
    ) == ""
