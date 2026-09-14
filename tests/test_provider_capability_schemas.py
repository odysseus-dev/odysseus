from src import model_capabilities as mc
from src import provider_capability_schemas as pcs
from src.model_capability_readers import (
    CANONICAL_MODEL_SHAPE_VERSION,
    anthropic,
    chatgpt_subscription,
    cohere,
    copilot,
    generic_openai,
    huggingface,
    mistral,
    records_from_payload,
    sglang,
)


def _openrouter_payload(*items):
    return {
        "data": list(items)
        or [
            {
                "id": "provider/model",
                "architecture": {"modality": "text->text"},
                "canonical_slug": "provider/model",
                "pricing": {"prompt": "0.1", "completion": "0.2"},
                "supported_parameters": ["tools", "temperature"],
                "top_provider": {"context_length": 32768},
            }
        ]
    }


def test_provider_identity_and_catalog_shape_are_resolved_separately():
    google_payload = {
        "models": [
            {
                "name": "models/example",
                "supportedGenerationMethods": ["generateContent"],
            }
        ]
    }

    explicit = pcs.resolve_provider(google_payload, provider="openrouter")
    host = pcs.resolve_provider(google_payload, base_url="https://api.mistral.ai/v1")
    native = pcs.resolve_provider(google_payload)
    fallback = pcs.resolve_provider([{"id": "future-model", "future": {"x": True}}])
    unknown = pcs.resolve_provider({"future": [{"not_an_identity": True}]})

    assert explicit.to_dict() == {
        "provider": "openrouter",
        "provider_source": pcs.PROVIDER_SOURCE_EXPLICIT,
        "shape": "fallback.models.envelope.v1",
        "fallback": True,
    }
    assert host.to_dict() == {
        "provider": "mistral",
        "provider_source": pcs.PROVIDER_SOURCE_HOST,
        "shape": "fallback.models.envelope.v1",
        "fallback": True,
    }
    assert native.to_dict() == {
        "provider": "google",
        "provider_source": pcs.PROVIDER_SOURCE_PAYLOAD,
        "shape": "google.generative-language.models.v1beta",
        "fallback": False,
    }
    assert fallback.to_dict() == {
        "provider": pcs.PROVIDER_UNKNOWN,
        "provider_source": pcs.PROVIDER_SOURCE_UNKNOWN,
        "shape": "fallback.models.list.v1",
        "fallback": True,
    }
    assert unknown.to_dict() == {
        "provider": pcs.PROVIDER_UNKNOWN,
        "provider_source": pcs.PROVIDER_SOURCE_UNKNOWN,
        "shape": "",
        "fallback": False,
    }


def test_provider_host_matching_rejects_lookalikes_and_does_not_use_ports():
    assert pcs.provider_from_host("https://api.openrouter.ai/v1") == "openrouter"
    assert pcs.provider_from_host("https://openrouter.ai.evil.test/v1") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:11434") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:1234") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:8000") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:30000") == pcs.PROVIDER_UNKNOWN


def test_transport_endpoint_kinds_do_not_preempt_host_or_payload_provider_identity():
    payload = _openrouter_payload()

    for endpoint_kind in ("auto", "local", "api", "proxy", "future-transport"):
        from_host = pcs.resolve_provider(
            payload,
            endpoint_kind=endpoint_kind,
            base_url="https://api.openrouter.ai/v1",
        )
        from_payload = pcs.resolve_provider(payload, endpoint_kind=endpoint_kind)

        assert from_host.provider_id == "openrouter"
        assert from_host.provider_source == pcs.PROVIDER_SOURCE_HOST
        assert from_payload.provider_id == "openrouter"
        assert from_payload.provider_source == pcs.PROVIDER_SOURCE_PAYLOAD

    assert pcs.provider_from_endpoint_kind("ollama") == "ollama"
    assert pcs.provider_from_endpoint_kind("llama.cpp") == "llamacpp"
    assert pcs.provider_from_endpoint_kind("proxy") == pcs.PROVIDER_UNKNOWN


def test_provider_aliases_only_normalize_explicit_identity():
    assert pcs.normalize_provider_id("opencode-go") == "opencode"
    assert pcs.normalize_provider_id("opencode-zen") == "opencode"
    assert pcs.normalize_provider_id("nvidia-nim") == "nvidia"
    assert pcs.normalize_provider_id("tgi") == "text_generation_inference"
    assert pcs.normalize_provider_id("llama.cpp") == "llamacpp"
    assert pcs.normalize_provider_id("Z.AI") == "zai"
    assert pcs.normalize_provider_id("future-provider") == "future_provider"


def test_unregistered_explicit_provider_is_preserved_but_stays_on_fallback():
    resolution = pcs.resolve_provider(
        {"data": [{"id": "future-model", "capabilities": {"tools": True}}]},
        provider="future-provider",
    )
    records = records_from_payload(
        {"data": [{"id": "future-model", "capabilities": {"tools": True}}]},
        vendor="future-provider",
    )

    assert resolution.to_dict() == {
        "provider": "future_provider",
        "provider_source": pcs.PROVIDER_SOURCE_EXPLICIT,
        "shape": "fallback.models.data.v1",
        "fallback": True,
    }
    assert records[0].vendor == "future_provider"
    assert records[0].capability.family == mc.FAMILY_UNKNOWN
    assert records[0].capability.capabilities == ()


def test_native_catalog_shapes_resolve_with_required_provider_context():
    cases = (
        (
            _openrouter_payload(),
            "openrouter",
            "openrouter.models.rich.v1",
        ),
        (
            {"models": [{"key": "local/model", "type": "llm", "capabilities": {"vision": True}}]},
            "lmstudio",
            "lmstudio.models.native.v1",
        ),
        (
            {"data": [{"id": "legacy", "type": "vlm", "arch": "gemma"}]},
            "lmstudio",
            "lmstudio.models.native.v0",
        ),
        (
            {"models": [{"name": "local", "digest": "abc", "details": {"family": "qwen3"}}]},
            "ollama",
            "ollama.tags.v1",
        ),
        (
            {"capabilities": ["completion", "vision"], "model_info": {"x.context_length": 4096}},
            "ollama",
            "ollama.show.v1",
        ),
        (
            {
                "model_alias": "local",
                "default_generation_settings": {"n_ctx": 4096},
                "chat_template_caps": {"supports_tools": True},
            },
            "llamacpp",
            "llamacpp.props.v1",
        ),
        (
            {"data": [{"id": "mistral", "capabilities": {"completion_chat": True, "vision": False}}]},
            "mistral",
            "mistral.models.rich.v1",
        ),
        (
            {
                "data": [
                    {
                        "id": "copilot-model",
                        "model_picker_enabled": True,
                        "capabilities": {"supports": {"tool_calls": True}},
                    }
                ]
            },
            "copilot",
            "github-copilot.models.v1",
        ),
        (
            {
                "model_path": "org/model",
                "tokenizer_path": "org/model",
                "is_generation": True,
                "has_image_understanding": False,
            },
            "sglang",
            "sglang.model-info.v2",
        ),
        (
            {
                "object": "list",
                "data": [
                    {
                        "id": "served-model",
                        "object": "model",
                        "owned_by": "vllm",
                        "root": "org/model",
                        "max_model_len": 131072,
                        "permission": [],
                    }
                ],
            },
            "vllm",
            "vllm.models.openai.v1",
        ),
        (
            {"models": [{"slug": "gpt-example", "visibility": "list", "priority": 1}]},
            "chatgpt_subscription",
            "chatgpt-subscription.codex-models.v1",
        ),
        (
            {"models": [{"name": "command-example", "endpoints": ["chat"], "context_length": 131072}]},
            "cohere",
            "cohere.models.rich.v1",
        ),
        (
            {
                "object": "list",
                "data": [{"id": "MiniMax-M2", "object": "model", "owned_by": "minimax"}],
            },
            "minimax",
            "minimax.models.identity.v1",
        ),
    )

    explicit_context_providers = {
        "anthropic",
        "chatgpt_subscription",
        "cohere",
        "huggingface",
        "lmstudio",
        "mistral",
        "ollama",
    }
    for payload, expected_provider, expected_shape in cases:
        explicit_provider = (
            expected_provider
            if expected_provider in explicit_context_providers
            else None
        )
        resolution = pcs.resolve_provider(payload, provider=explicit_provider)
        assert resolution.provider_id == expected_provider
        assert resolution.provider_source == (
            pcs.PROVIDER_SOURCE_EXPLICIT
            if explicit_provider
            else pcs.PROVIDER_SOURCE_PAYLOAD
        )
        assert resolution.shape_id == expected_shape
        assert resolution.fallback is False


def test_generic_ollama_like_fields_require_provider_context():
    show_payload = {
        "name": "foreign-model",
        "capabilities": ["vision"],
        "parameters": {},
    }
    tags_payload = {"models": [{"name": "foreign-model", "digest": None}]}

    inferred = pcs.resolve_provider(show_payload)
    contextual = pcs.resolve_provider(show_payload, provider="ollama")

    assert inferred.provider_id == pcs.PROVIDER_UNKNOWN
    assert inferred.shape_id == ""
    assert inferred.fallback is False
    assert records_from_payload(show_payload) == ()
    assert contextual.provider_id == "ollama"
    assert contextual.shape_id == "ollama.show.v1"
    assert contextual.fallback is False
    contextual_record = records_from_payload(show_payload, vendor="ollama")[0]
    assert contextual_record.capability.capabilities == (mc.CAP_VISION,)

    inferred_tags = pcs.resolve_provider(tags_payload)
    contextual_tags = pcs.resolve_provider(tags_payload, provider="ollama")
    assert inferred_tags.provider_id == pcs.PROVIDER_UNKNOWN
    assert inferred_tags.shape_id == "fallback.models.envelope.v1"
    assert inferred_tags.fallback is True
    assert contextual_tags.provider_id == "ollama"
    assert contextual_tags.shape_id == "ollama.tags.v1"
    assert contextual_tags.fallback is False


def test_singleton_native_reader_ignores_competing_list_envelopes():
    record = records_from_payload(
        {
            "model": "show-model",
            "capabilities": ["completion", "vision"],
            "model_info": {"family.context_length": 4096},
            "models": [
                {
                    "name": "shadow-model",
                    "digest": "abc",
                    "details": {"family": "shadow"},
                }
            ],
        },
        vendor="ollama",
    )[0]

    assert record.model_id == "show-model"
    assert record.catalog_shape_id == "ollama.show.v1"
    assert record.capability.capabilities == (mc.CAP_VISION,)


def test_ambiguous_common_fields_do_not_infer_provider_from_payload_alone():
    cases = (
        ({"data": [{"id": "generic", "architecture": {}}]}, "openrouter"),
        ({"data": [{"id": "generic", "supported_parameters": ["tools"]}]}, "openrouter"),
        (
            {"data": [{"id": "generic", "capabilities": {"completion_chat": True}}]},
            "mistral",
        ),
        ({"data": [{"id": "generic", "type": "llm", "arch": "future"}]}, "lmstudio"),
        (
            {"models": [{"key": "generic", "type": "llm", "capabilities": {}}]},
            "lmstudio",
        ),
        (
            {"models": [{"name": "generic", "endpoints": ["chat"], "context_length": 4096}]},
            "cohere",
        ),
        (
            {"models": [{"slug": "generic", "visibility": "list", "priority": 1}]},
            "chatgpt_subscription",
        ),
    )

    for payload, provider_id in cases:
        inferred = pcs.resolve_provider(payload)
        contextual = pcs.resolve_provider(payload, provider=provider_id)

        assert inferred.provider_id == pcs.PROVIDER_UNKNOWN
        assert inferred.fallback is True
        assert contextual.provider_id == provider_id


def test_payload_matching_multiple_providers_degrades_to_fallback():
    payload = _openrouter_payload(
        {
            **_openrouter_payload()["data"][0],
            "model_picker_enabled": True,
            "capabilities": {"supports": {"tool_calls": True}},
        }
    )
    resolution = pcs.resolve_provider(payload)
    record = records_from_payload(payload)[0]

    assert resolution.provider_id == pcs.PROVIDER_UNKNOWN
    assert resolution.shape_id == "fallback.models.data.v1"
    assert resolution.fallback is True
    assert record.vendor == pcs.PROVIDER_UNKNOWN
    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.capabilities == ()
    assert record.fallback is True


def test_generic_pipeline_tag_does_not_select_huggingface_without_provider_context():
    payload = {"id": "generic-model", "pipeline_tag": "image-text-to-text"}
    inferred = pcs.resolve_provider(payload)
    contextual = pcs.resolve_provider(payload, provider="huggingface")

    assert inferred.provider_id == pcs.PROVIDER_UNKNOWN
    assert inferred.shape_id == ""
    assert inferred.fallback is False
    assert records_from_payload(payload) == ()
    assert contextual.provider_id == "huggingface"
    assert contextual.shape_id == "huggingface.hub.model-info.v1"
    assert contextual.fallback is False


def test_openrouter_payload_detection_requires_the_compound_official_shape():
    complete = _openrouter_payload()
    assert pcs.resolve_provider(complete).shape_id == "openrouter.models.rich.v1"

    item = complete["data"][0]
    for required_field in (
        "architecture",
        "canonical_slug",
        "pricing",
        "supported_parameters",
        "top_provider",
    ):
        partial = _openrouter_payload(
            {key: value for key, value in item.items() if key != required_field}
        )
        resolution = pcs.resolve_provider(partial)

        assert resolution.provider_id == pcs.PROVIDER_UNKNOWN
        assert resolution.shape_id == "fallback.models.data.v1"
        assert resolution.fallback is True


def test_wrong_native_field_types_degrade_to_explicit_fallback_inventory():
    malformed_cohere = pcs.resolve_provider(
        {"models": [{"name": "future", "endpoints": "chat", "context_length": 4096}]},
        provider="cohere",
    )
    malformed_mistral = pcs.resolve_provider(
        {"data": [{"id": "future", "capabilities": ["completion_chat"]}]},
        provider="mistral",
    )

    assert malformed_cohere.provider_id == "cohere"
    assert malformed_cohere.shape_id == "fallback.models.envelope.v1"
    assert malformed_cohere.fallback is True
    assert malformed_mistral.provider_id == "mistral"
    assert malformed_mistral.shape_id == "fallback.models.data.v1"
    assert malformed_mistral.fallback is True


def test_explicit_fallback_and_mixed_native_payloads_are_normalized_per_item():
    malformed = {"models": [{"name": "unsafe", "endpoints": "chat", "context_length": 4096}]}
    malformed_record = records_from_payload(malformed, vendor="cohere")[0]

    assert malformed_record.vendor == "cohere"
    assert malformed_record.capability.family == mc.FAMILY_UNKNOWN
    assert dict(malformed_record.capability.limits) == {}
    assert malformed_record.catalog_shape_id == "fallback.models.envelope.v1"
    assert malformed_record.fallback is True

    valid_item = {
        "name": "native",
        "endpoints": ["chat"],
        "context_length": 131072,
    }
    native, fallback = records_from_payload(
        {"models": [valid_item, malformed["models"][0]]},
        vendor="cohere",
    )

    assert native.model_id == "native"
    assert native.capability.family == mc.FAMILY_CHAT
    assert dict(native.capability.limits) == {"context_tokens": 131072}
    assert native.catalog_shape_id == "cohere.models.rich.v1"
    assert native.fallback is False
    assert fallback.model_id == "unsafe"
    assert fallback.capability.family == mc.FAMILY_UNKNOWN
    assert dict(fallback.capability.limits) == {}
    assert fallback.catalog_shape_id == "fallback.models.envelope.v1"
    assert fallback.fallback is True


def test_provider_specific_reader_is_not_used_for_a_different_fallback_envelope():
    record = records_from_payload(
        [{"id": "untrusted", "architecture": {"modality": "text+image->text"}}],
        vendor="openrouter",
    )[0]

    assert record.vendor == "openrouter"
    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.capabilities == ()
    assert record.catalog_shape_id == "fallback.models.list.v1"
    assert record.fallback is True


def test_selected_native_envelope_ignores_an_unrelated_alternate_envelope():
    record = records_from_payload(
        {
            "data": [{"id": "unrelated-openai-card"}],
            "models": [
                {
                    "key": "native-lmstudio-card",
                    "type": "llm",
                    "capabilities": {"vision": True},
                }
            ],
        },
        vendor="lmstudio",
    )[0]

    assert record.model_id == "native-lmstudio-card"
    assert record.vendor == "lmstudio"
    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.capabilities == (mc.CAP_VISION,)
    assert record.catalog_shape_id == "lmstudio.models.native.v1"
    assert record.fallback is False


def test_same_provider_shape_tie_prefers_declared_modern_envelope():
    record = records_from_payload(
        {
            "data": [
                {
                    "id": "legacy-v0-card",
                    "type": "vlm",
                    "arch": "legacy",
                }
            ],
            "models": [
                {
                    "key": "modern-v1-card",
                    "type": "llm",
                    "capabilities": {"vision": True},
                }
            ],
        },
        vendor="lmstudio",
    )[0]

    assert record.model_id == "modern-v1-card"
    assert record.catalog_shape_id == "lmstudio.models.native.v1"
    assert record.capability.capabilities == (mc.CAP_VISION,)


def test_selected_fallback_envelope_is_not_shadowed_by_empty_data():
    record = records_from_payload(
        {
            "data": [],
            "models": [{"id": "fallback-model", "capabilities": {"tools": True}}],
        }
    )[0]

    assert record.model_id == "fallback-model"
    assert record.vendor == pcs.PROVIDER_UNKNOWN
    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.capabilities == ()
    assert record.catalog_shape_id == "fallback.models.envelope.v1"
    assert record.fallback is True


def test_fallback_reader_is_identity_only_even_for_dangerous_looking_fields():
    payload = [
        {
            "id": "future-rich-model",
            "type": "chat",
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
            "capabilities": {"supports": {"tools": True, "reasoning": True}},
            "supported_parameters": ["tools", "structured_outputs", "temperature"],
            "max_model_len": 131072,
        },
        {"key": "key-only-model", "pipeline_tag": "text-to-image"},
        {"slug": "slug-only-model", "modality": "text_to_image"},
    ]

    direct = generic_openai.records_from_payload(payload)
    wrapped = records_from_payload(payload, vendor="together")

    assert [record.model_id for record in direct] == [
        "future-rich-model",
        "key-only-model",
        "slug-only-model",
    ]
    for record in (*direct, *wrapped):
        assert record.capability.family == mc.FAMILY_UNKNOWN
        assert record.capability.capabilities == ()
        assert dict(record.capability.limits) == {}
        assert record.deterministic_controls == ()

    lean = wrapped[0].to_dict()
    assert lean == {
        "schema_version": CANONICAL_MODEL_SHAPE_VERSION,
        "provider": "together",
        "model": "future-rich-model",
        "stable_id": "together|global|future-rich-model",
        "family": "unknown",
        "task": "unknown",
        "modalities": {"input": [], "output": []},
        "features": [],
        "limits": {},
        "controls": [],
        "evidence": {
            "source": "provider_reader",
            "confidence": "unknown",
            "provider_source": "explicit",
            "shape": "fallback.models.list.v1",
            "fallback": True,
        },
    }
    assert wrapped[0].to_dict(include_raw=True)["raw"] == payload[0]


def test_fallback_reader_fails_soft_for_null_and_malformed_envelopes():
    for payload in (
        {"data": None},
        {"models": None},
        {"data": "not-a-list"},
        [None, "model", 42, {"id": None}],
        None,
    ):
        assert generic_openai.records_from_payload(payload) == ()


def test_structured_identity_values_are_not_stringified_into_fallback_records():
    for key in ("id", "name", "model", "key", "slug"):
        payload = [{key: {"nested": "model"}}]

        assert pcs.resolve_provider(payload).shape_id == ""
        assert generic_openai.records_from_payload(payload) == ()
        assert records_from_payload(payload, vendor="future-provider") == ()


def test_native_readers_skip_structured_identity_candidates():
    google_record = records_from_payload(
        {
            "models": [
                {
                    "baseModelId": {"nested": "bad"},
                    "name": "models/good-google-id",
                    "supportedGenerationMethods": ["embedContent"],
                }
            ]
        }
    )[0]
    huggingface_record = records_from_payload(
        {
            "modelId": {"nested": "bad"},
            "id": "org/good-hf-id",
            "pipeline_tag": "text-to-image",
        },
        vendor="huggingface",
    )[0]
    llamacpp_record = records_from_payload(
        {
            "model_alias": {"nested": "bad"},
            "model_path": "/models/good-llama.gguf",
            "default_generation_settings": {},
            "chat_template_caps": {"supports_vision": True},
        },
        vendor="llamacpp",
    )[0]
    sglang_record = records_from_payload(
        {
            "served_model_name": {"nested": "bad"},
            "model_path": "/models/good-sglang",
            "is_generation": True,
            "has_image_understanding": True,
        },
        vendor="sglang",
    )[0]

    assert google_record.model_id == "good-google-id"
    assert huggingface_record.model_id == "org/good-hf-id"
    assert llamacpp_record.model_id == "good-llama.gguf"
    assert sglang_record.model_id == "good-sglang"
    assert chatgpt_subscription.record_from_model({"slug": {"nested": "bad"}}) is None
    assert cohere.record_from_model(
        {"name": {"nested": "bad"}, "endpoints": ["chat"]}
    ) is None


def test_native_singleton_and_bare_list_shapes_reach_their_readers():
    google_records = records_from_payload(
        {
            "name": "models/gemini-embed",
            "supportedGenerationMethods": ["embedContent"],
        },
        vendor="google",
    )
    google_base_id_records = records_from_payload(
        {
            "baseModelId": "gemini-base-only",
            "supportedGenerationMethods": ["embedContent"],
        },
        vendor="google",
    )
    huggingface_records = records_from_payload(
        [{"modelId": "org/model", "pipeline_tag": "text-generation"}],
        vendor="huggingface",
    )
    llamacpp_records = records_from_payload(
        {
            "models": [
                {
                    "id": "served-model",
                    "capabilities": ["chat", "tools"],
                }
            ]
        },
        vendor="llamacpp",
    )

    assert google_records[0].model_id == "gemini-embed"
    assert google_records[0].capability.family == mc.FAMILY_EMBEDDING
    assert google_records[0].catalog_shape_id == (
        "google.generative-language.model.v1beta"
    )
    assert google_base_id_records[0].model_id == "gemini-base-only"
    assert google_base_id_records[0].capability.family == mc.FAMILY_EMBEDDING
    assert huggingface_records[0].model_id == "org/model"
    assert huggingface_records[0].capability.family == mc.FAMILY_CHAT
    assert huggingface_records[0].catalog_shape_id == (
        "huggingface.hub.model-info-list.v1"
    )
    assert llamacpp_records[0].model_id == "served-model"
    assert llamacpp_records[0].capability.family == mc.FAMILY_CHAT
    assert llamacpp_records[0].capability.capabilities == (mc.CAP_TOOL_CALL,)
    assert llamacpp_records[0].catalog_shape_id == "llamacpp.models.native.v1"


def test_huggingface_openai_serving_envelope_stays_identity_only():
    payload = {
        "data": [
            {
                "id": "served-model",
                "pipeline_tag": "text-to-image",
            }
        ]
    }

    direct = huggingface.records_from_payload(payload)
    wrapped = records_from_payload(payload, vendor="huggingface")

    assert direct == ()
    assert wrapped[0].model_id == "served-model"
    assert wrapped[0].capability.family == mc.FAMILY_UNKNOWN
    assert wrapped[0].capability.capabilities == ()
    assert wrapped[0].fallback is True


def test_generic_model_resource_fields_do_not_infer_anthropic_identity():
    payload = {
        "data": [
            {
                "id": "foreign-model",
                "type": "model",
                "display_name": "Foreign Model",
                "created_at": "2026-01-01T00:00:00Z",
                "capabilities": {"tools": True},
            }
        ]
    }

    resolution = pcs.resolve_provider(payload)
    records = records_from_payload(payload)

    assert resolution.provider_id == pcs.PROVIDER_UNKNOWN
    assert resolution.shape_id == "fallback.models.data.v1"
    assert resolution.fallback is True
    assert records[0].vendor == pcs.PROVIDER_UNKNOWN
    assert records[0].fallback is True
    assert records[0].capability.capabilities == ()


def test_mistral_reader_maps_per_model_capabilities_without_provider_inheritance():
    records = mistral.records_from_payload(
        {
            "data": [
                {
                    "id": "vision-chat",
                    "capabilities": {
                        "completion_chat": True,
                        "function_calling": True,
                        "vision": True,
                        "classification": False,
                    },
                    "max_context_length": 32768,
                },
                {
                    "id": "classifier",
                    "capabilities": {
                        "completion_chat": False,
                        "classification": True,
                        "vision": False,
                    },
                },
                {"id": "future-card", "capabilities": {"future_only": True}},
            ]
        }
    )

    assert records[0].capability.family == mc.FAMILY_CHAT
    assert records[0].capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert records[0].capability.capabilities == (mc.CAP_VISION, mc.CAP_TOOL_CALL)
    assert dict(records[0].capability.limits) == {"context_tokens": 32768}
    assert records[1].capability.family == mc.FAMILY_CLASSIFICATION
    assert records[2].capability.family == mc.FAMILY_UNKNOWN


def test_copilot_reader_uses_picker_and_nested_supports_shape():
    record = copilot.records_from_payload(
        {
            "data": [
                {
                    "id": "picker-model",
                    "model_picker_enabled": True,
                    "capabilities": {"supports": {"tool_calls": True, "vision": True}},
                    "limits": {"max_prompt_tokens": 64000, "max_output_tokens": 8192},
                }
            ]
        }
    )[0]

    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.capabilities == (mc.CAP_TOOL_CALL, mc.CAP_VISION)
    assert dict(record.capability.limits) == {"input_tokens": 64000, "output_tokens": 8192}


def test_copilot_reader_ignores_unverified_support_aliases():
    record = records_from_payload(
        {
            "data": [
                {
                    "id": "future-supports-model",
                    "model_picker_enabled": True,
                    "capabilities": {
                        "supports": {
                            "tools": True,
                            "reasoning": True,
                            "structured_outputs": True,
                        }
                    },
                }
            ]
        },
        vendor="copilot",
    )[0]

    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.capabilities == ()


def test_copilot_catalog_uses_picker_selection_with_no_picker_fallback():
    def payload(*picker_values):
        return {
            "data": [
                {
                    "id": f"model-{index}",
                    "model_picker_enabled": picker_enabled,
                    "capabilities": {"supports": {}},
                }
                for index, picker_enabled in enumerate(picker_values)
            ]
        }

    selected = records_from_payload(payload(False, True, False), vendor="copilot")
    fallback = records_from_payload(payload(False, False), vendor="copilot")

    assert [record.model_id for record in selected] == ["model-1"]
    assert [record.model_id for record in fallback] == ["model-0", "model-1"]


def test_chatgpt_catalog_applies_visibility_priority_and_slug_deduplication():
    records = records_from_payload(
        {
            "models": [
                {"slug": "hidden", "visibility": "hidden", "priority": 0},
                {"slug": "later", "visibility": "list", "priority": 20},
                {
                    "slug": "duplicate",
                    "visibility": "list",
                    "priority": 30,
                    "title": "lower-precedence",
                },
                {"slug": "first", "visibility": "list", "priority": 1},
                {
                    "slug": "duplicate",
                    "visibility": "list",
                    "priority": 5,
                    "title": "selected",
                },
                {"slug": "unranked", "visibility": "list", "priority": float("inf")},
            ]
        },
        vendor="chatgpt_subscription",
    )

    assert [record.model_id for record in records] == [
        "first",
        "duplicate",
        "later",
        "unranked",
    ]
    assert records[1].display_name == "selected"


def test_sglang_model_info_maps_native_generation_flags_only():
    generation = sglang.records_from_payload(
        {
            "model_path": "org/vision-model",
            "tokenizer_path": "org/vision-model",
            "is_generation": True,
            "has_image_understanding": True,
            "has_audio_understanding": True,
            "preferred_sampling_params": {"temperature": 0.2, "top_p": 0.9},
        }
    )[0]
    pooling = sglang.records_from_payload(
        {
            "model_path": "org/pooling-model",
            "tokenizer_path": "org/pooling-model",
            "is_generation": False,
            "has_image_understanding": False,
        }
    )[0]

    assert generation.capability.family == mc.FAMILY_CHAT
    assert generation.capability.modalities.input == (
        mc.MODALITY_TEXT,
        mc.MODALITY_IMAGE,
        mc.MODALITY_AUDIO,
    )
    assert generation.capability.capabilities == (mc.CAP_VISION, mc.CAP_AUDIO_INPUT)
    assert [control.control for control in generation.deterministic_controls] == [
        mc.CONTROL_TEMPERATURE,
        mc.CONTROL_TOP_P,
    ]
    assert pooling.capability.family == mc.FAMILY_UNKNOWN


def test_sglang_openai_catalog_preserves_only_valid_native_context_limit():
    valid = records_from_payload(
        {
            "data": [
                {
                    "id": "served-model",
                    "owned_by": "sglang",
                    "root": "org/model",
                    "max_model_len": 131072,
                }
            ]
        }
    )[0]
    malformed = [
        records_from_payload(
            {
                "data": [
                    {
                        "id": "served-model",
                        "owned_by": "sglang",
                        "root": "org/model",
                        "max_model_len": value,
                    }
                ]
            }
        )[0]
        for value in (0, True, 1.5, float("inf"))
    ]

    assert valid.vendor == "sglang"
    assert valid.capability.family == mc.FAMILY_UNKNOWN
    assert valid.capability.capabilities == ()
    assert dict(valid.capability.limits) == {"context_tokens": 131072}
    assert valid.catalog_shape_id == "sglang.models.openai.v1"
    assert valid.fallback is False
    assert all(dict(record.capability.limits) == {} for record in malformed)


def test_identity_only_native_catalogs_remain_unknown():
    anthropic_record = anthropic.records_from_payload(
        {
            "data": [
                {
                    "id": "claude-example",
                    "type": "model",
                    "display_name": "Claude Example",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        }
    )[0]
    chatgpt_record = chatgpt_subscription.records_from_payload(
        {"models": [{"slug": "gpt-example", "visibility": "list", "priority": 1}]}
    )[0]
    minimax_record = records_from_payload(
        {
            "object": "list",
            "data": [{"id": "MiniMax-M2", "object": "model", "owned_by": "minimax"}],
        }
    )[0]

    assert anthropic_record.capability.family == mc.FAMILY_UNKNOWN
    assert chatgpt_record.capability.family == mc.FAMILY_UNKNOWN
    assert minimax_record.vendor == "minimax"
    assert minimax_record.capability.family == mc.FAMILY_UNKNOWN


def test_huggingface_reader_maps_provider_specific_pipeline_metadata():
    record = huggingface.records_from_payload(
        {
            "modelId": "org/vision-model",
            "pipeline_tag": "image-text-to-text",
            "config": {"model_type": "future_vlm"},
            "tags": ["untrusted-prose-tag"],
        }
    )[0]

    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert record.capability.capabilities == (mc.CAP_VISION,)
    assert record.capability.source == mc.SOURCE_COOKBOOK_HF
    assert record.capability.confidence == mc.CONFIDENCE_REGISTRY


def test_huggingface_optional_pipeline_tag_preserves_identity_only_records():
    cases = (
        (
            {"modelId": "org/no-pipeline-tag"},
            "huggingface.hub.model-info.v1",
        ),
        (
            {"modelId": "org/null-pipeline-tag", "pipeline_tag": None},
            "huggingface.hub.model-info.v1",
        ),
        (
            [{"modelId": "org/list-no-pipeline-tag"}],
            "huggingface.hub.model-info-list.v1",
        ),
        (
            [{"modelId": "org/list-null-pipeline-tag", "pipeline_tag": None}],
            "huggingface.hub.model-info-list.v1",
        ),
    )

    for payload, shape_id in cases:
        resolution = pcs.resolve_provider(payload, provider="huggingface")
        direct = huggingface.records_from_payload(payload)
        wrapped = records_from_payload(payload, vendor="huggingface")

        assert resolution.shape_id == shape_id
        assert resolution.fallback is False
        assert len(direct) == 1
        assert len(wrapped) == 1
        assert wrapped[0].model_id == direct[0].model_id
        assert wrapped[0].capability.family == mc.FAMILY_UNKNOWN
        assert wrapped[0].capability.capabilities == ()
        assert wrapped[0].catalog_shape_id == shape_id
        assert wrapped[0].fallback is False

    # An identity-only singleton remains insufficient to infer Hugging Face
    # without configured provider or host context.
    unscoped = {"modelId": "org/unscoped"}
    assert pcs.resolve_provider(unscoped).provider_id == pcs.PROVIDER_UNKNOWN
    assert records_from_payload(unscoped) == ()


def test_cohere_reader_maps_only_native_endpoint_and_limit_fields():
    chat, ambiguous = cohere.records_from_payload(
        {
            "models": [
                {
                    "name": "command-example",
                    "endpoints": ["chat", "generate"],
                    "context_length": 131072,
                    "sampling_defaults": {"temperature": 0.3, "p": 0.9, "k": 40},
                    "features": ["unmapped-future-feature"],
                },
                {
                    "name": "multi-endpoint-example",
                    "endpoints": ["chat", "embed"],
                    "context_length": 4096,
                },
            ]
        }
    )

    assert chat.capability.family == mc.FAMILY_CHAT
    assert dict(chat.capability.limits) == {"context_tokens": 131072}
    assert [control.control for control in chat.deterministic_controls] == [
        mc.CONTROL_TEMPERATURE,
        mc.CONTROL_TOP_P,
        mc.CONTROL_TOP_K,
    ]
    assert chat.raw["features"] == ["unmapped-future-feature"]
    assert ambiguous.capability.family == mc.FAMILY_UNKNOWN


def test_reader_wrapper_adds_one_lean_evidence_object():
    record = records_from_payload(
        {
            "data": [
                {
                    "id": "mistral-model",
                    "capabilities": {"completion_chat": True, "function_calling": True},
                }
            ]
        },
        base_url="https://api.mistral.ai/v1",
    )[0]
    serialized = record.to_dict()

    assert record.vendor == "mistral"
    assert serialized["schema_version"] == 1
    assert serialized["provider"] == "mistral"
    assert serialized["features"] == [mc.CAP_TOOL_CALL]
    assert serialized["evidence"] == {
        "source": mc.SOURCE_PROVIDER_READER,
        "confidence": mc.CONFIDENCE_PROVIDER_REPORTED,
        "provider_source": pcs.PROVIDER_SOURCE_HOST,
        "shape": "mistral.models.rich.v1",
        "fallback": False,
    }
    assert "capability" not in serialized
    assert "capability_assertions" not in serialized
    assert "deterministic_controls" not in serialized
