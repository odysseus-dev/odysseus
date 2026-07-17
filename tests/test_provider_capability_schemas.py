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


def test_current_native_catalog_shapes_are_discriminating():
    cases = (
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

    for payload, expected_provider, expected_shape in cases:
        resolution = pcs.resolve_provider(payload)
        assert resolution.provider_id == expected_provider
        assert resolution.provider_source == pcs.PROVIDER_SOURCE_PAYLOAD
        assert resolution.shape_id == expected_shape
        assert resolution.fallback is False


def test_wrong_native_field_types_degrade_to_explicit_fallback_inventory():
    malformed_cohere = pcs.resolve_provider(
        {"models": [{"name": "future", "endpoints": "chat", "context_length": 4096}]}
    )
    malformed_mistral = pcs.resolve_provider(
        {"data": [{"id": "future", "capabilities": ["completion_chat"]}]}
    )

    assert malformed_cohere.provider_id == pcs.PROVIDER_UNKNOWN
    assert malformed_cohere.shape_id == "fallback.models.envelope.v1"
    assert malformed_cohere.fallback is True
    assert malformed_mistral.provider_id == pcs.PROVIDER_UNKNOWN
    assert malformed_mistral.shape_id == "fallback.models.data.v1"
    assert malformed_mistral.fallback is True


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
        }
    )[0]
    serialized = record.to_dict()

    assert record.vendor == "mistral"
    assert serialized["schema_version"] == 1
    assert serialized["provider"] == "mistral"
    assert serialized["features"] == [mc.CAP_TOOL_CALL]
    assert serialized["evidence"] == {
        "source": mc.SOURCE_PROVIDER_READER,
        "confidence": mc.CONFIDENCE_PROVIDER_REPORTED,
        "provider_source": pcs.PROVIDER_SOURCE_PAYLOAD,
        "shape": "mistral.models.rich.v1",
        "fallback": False,
    }
    assert "capability" not in serialized
    assert "capability_assertions" not in serialized
    assert "deterministic_controls" not in serialized
