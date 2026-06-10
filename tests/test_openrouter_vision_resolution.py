from src import document_processor as dp


def test_cached_vision_model_prefers_free_openrouter_vl_model():
    models = [
        "minimax/minimax-m3",
        "openai/gpt-4o",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "qwen/qwen2.5-vl-72b-instruct",
    ]

    assert dp._choose_cached_vision_model(models) == "nvidia/nemotron-nano-12b-v2-vl:free"


def test_cached_openrouter_vision_model_requires_free_when_requested():
    paid_only = [
        "openai/gpt-4o",
        "qwen/qwen2.5-vl-72b-instruct",
    ]
    mixed = paid_only + ["qwen/qwen-2.5-vl-7b-instruct:free"]

    assert dp._choose_cached_vision_model(paid_only, require_free=True) is None
    assert (
        dp._choose_cached_vision_model(mixed, require_free=True)
        == "qwen/qwen-2.5-vl-7b-instruct:free"
    )


def test_configured_short_name_can_resolve_from_cached_provider_model_id():
    models = [
        "minimax/minimax-m3",
        "qwen/qwen2.5-vl-72b-instruct",
    ]

    assert dp._choose_cached_vision_model(models, "qwen2.5-vl") == "qwen/qwen2.5-vl-72b-instruct"


def test_configured_short_name_requires_free_on_openrouter_cache():
    models = [
        "qwen/qwen2.5-vl-72b-instruct",
        "qwen/qwen-2.5-vl-7b-instruct:free",
    ]

    assert (
        dp._choose_cached_vision_model(models, "qwen2.5-vl", require_free=True)
        == "qwen/qwen-2.5-vl-7b-instruct:free"
    )


def test_openrouter_paid_resolved_candidate_is_skipped(monkeypatch):
    from src import ai_interaction

    monkeypatch.setattr(dp, "_resolve_cached_vision_model", lambda *args, **kwargs: None)

    def fake_resolve_model(candidate):
        if candidate.endswith(":free"):
            raise ValueError("free candidate not available")
        if candidate == "gpt-4o":
            return (
                "https://openrouter.ai/api/v1/chat/completions",
                "openai/gpt-4o",
                {},
            )
        if candidate == "llava":
            return (
                "http://localhost:11434/v1/chat/completions",
                "llava",
                {},
            )
        raise ValueError("candidate not available")

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve_model)

    assert dp._resolve_vl_model("") == (
        "http://localhost:11434/v1/chat/completions",
        "llava",
        {},
    )


def test_openrouter_paid_vision_fallbacks_are_filtered():
    candidates = [
        ("https://openrouter.ai/api/v1/chat/completions", "openai/gpt-4o", {}),
        (
            "https://openrouter.ai/api/v1/chat/completions",
            "nvidia/nemotron-nano-12b-v2-vl:free",
            {},
        ),
        ("https://api.openai.com/v1/chat/completions", "gpt-4o", {}),
    ]

    assert dp._filter_openrouter_paid_fallbacks(candidates) == candidates[1:]
