from src import model_behavior_quirks as quirks
from src import model_capabilities as mc
from src import provider_capability_schemas as pcs
from src.model_capability_readers import (
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


def test_provider_resolution_order_explicit_then_host_then_native_then_general():
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
    general = pcs.resolve_provider([{"id": "future-model", "future": {"x": True}}])
    unknown = pcs.resolve_provider({"future": [{"not_an_identity": True}]})

    assert (explicit.provider_id, explicit.stage) == ("openrouter", pcs.RESOLUTION_EXPLICIT)
    assert (host.provider_id, host.stage) == ("mistral", pcs.RESOLUTION_HOST)
    assert (native.provider_id, native.stage) == ("google", pcs.RESOLUTION_NATIVE_SHAPE)
    assert native.catalog_shape.shape_id == "google.generative-language.models.v1beta"
    assert (general.provider_id, general.stage) == (
        pcs.PROVIDER_GENERIC_OPENAI,
        pcs.RESOLUTION_GENERAL_SHAPE,
    )
    assert (unknown.provider_id, unknown.stage) == (
        pcs.PROVIDER_UNKNOWN,
        pcs.RESOLUTION_UNKNOWN,
    )


def test_provider_host_matching_rejects_lookalikes_and_does_not_use_ports():
    assert pcs.provider_from_host("https://api.openrouter.ai/v1") == "openrouter"
    assert pcs.provider_from_host("https://openrouter.ai.evil.test/v1") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:11434") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:1234") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:8000") == pcs.PROVIDER_UNKNOWN
    assert pcs.provider_from_host("http://127.0.0.1:30000") == pcs.PROVIDER_UNKNOWN


def test_provider_aliases_collapse_runtime_names_without_url_path_guessing():
    assert pcs.normalize_provider_id("opencode-go") == "opencode"
    assert pcs.normalize_provider_id("opencode-zen") == "opencode"
    assert pcs.normalize_provider_id("nvidia-nim") == "nvidia"
    assert pcs.normalize_provider_id("tgi") == "text_generation_inference"
    assert pcs.normalize_provider_id("llama.cpp") == "llamacpp"
    assert pcs.normalize_provider_id("Z.AI") == "zai"


def test_current_native_catalog_shapes_are_discriminating_and_versioned():
    cases = (
        (
            {"models": [{"key": "local/model", "type": "llm", "capabilities": {"vision": True}}]},
            "lmstudio.models.native.v1",
        ),
        (
            {"data": [{"id": "legacy", "type": "vlm", "arch": "gemma"}]},
            "lmstudio.models.native.v0",
        ),
        (
            {"models": [{"name": "local", "digest": "abc", "details": {"family": "qwen3"}}]},
            "ollama.tags.v1",
        ),
        (
            {"capabilities": ["completion", "vision"], "model_info": {"x.context_length": 4096}},
            "ollama.show.v1",
        ),
        (
            {
                "model_alias": "local",
                "default_generation_settings": {"n_ctx": 4096},
                "chat_template_caps": {"supports_tools": True},
            },
            "llamacpp.props.v1",
        ),
        (
            {"data": [{"id": "mistral", "capabilities": {"completion_chat": True, "vision": False}}]},
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
            "github-copilot.models.v1",
        ),
        (
            {
                "model_path": "org/model",
                "tokenizer_path": "org/model",
                "is_generation": True,
                "has_image_understanding": False,
            },
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
            "vllm.models.openai.v1",
        ),
        (
            {"models": [{"slug": "gpt-example", "visibility": "list", "priority": 1}]},
            "chatgpt-subscription.codex-models.v1",
        ),
        (
            {
                "models": [
                    {
                        "name": "command-example",
                        "endpoints": ["chat"],
                        "context_length": 131072,
                    }
                ]
            },
            "cohere.models.rich.v1",
        ),
        (
            {
                "object": "list",
                "data": [
                    {
                        "id": "MiniMax-M2-example",
                        "object": "model",
                        "owned_by": "minimax",
                    }
                ],
            },
            "minimax.models.identity.v1",
        ),
    )

    for payload, expected_shape in cases:
        resolution = pcs.resolve_provider(payload)
        assert resolution.stage == pcs.RESOLUTION_NATIVE_SHAPE
        assert resolution.catalog_shape.shape_id == expected_shape


def test_native_shape_detection_rejects_wrong_field_types_before_general_fallback():
    malformed_cohere = pcs.resolve_provider(
        {"models": [{"name": "future", "endpoints": "chat", "context_length": 4096}]}
    )
    malformed_mistral = pcs.resolve_provider(
        {"data": [{"id": "future", "capabilities": ["completion_chat"]}]}
    )

    assert (malformed_cohere.provider_id, malformed_cohere.stage) == (
        pcs.PROVIDER_GENERIC_OPENAI,
        pcs.RESOLUTION_GENERAL_SHAPE,
    )
    assert (malformed_mistral.provider_id, malformed_mistral.stage) == (
        pcs.PROVIDER_GENERIC_OPENAI,
        pcs.RESOLUTION_GENERAL_SHAPE,
    )


def test_general_reader_promotes_only_explicit_structural_fields_and_accepts_bare_lists():
    records = generic_openai.records_from_payload(
        [
            {
                "id": "future-rich-model",
                "type": "chat",
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "supported_parameters": ["tools", "structured_outputs", "temperature"],
                "max_model_len": 131072,
                "future_capability": {"may_be_important_later": True},
            },
            {
                "id": "vision-reasoning-tools-in-the-name-only",
                "description": "Claims every capability in prose",
                "type": "image",
                "future_capability": True,
            },
        ]
    )

    rich, identity_only = records
    assert rich.capability.family == mc.FAMILY_CHAT
    assert rich.capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert rich.capability.capabilities == (
        mc.CAP_TOOL_CALL,
        mc.CAP_STRUCTURED_OUTPUT,
        mc.CAP_VISION,
    )
    assert dict(rich.capability.limits) == {"context_tokens": 131072}
    assert [control.control for control in rich.deterministic_controls] == [mc.CONTROL_TEMPERATURE]
    assert rich.raw["future_capability"] == {"may_be_important_later": True}

    assert identity_only.capability.family == mc.FAMILY_UNKNOWN
    assert identity_only.capability.capabilities == ()
    assert identity_only.raw["future_capability"] is True


def test_general_reader_fails_soft_for_null_and_malformed_envelopes():
    for payload in (
        {"data": None},
        {"models": None},
        {"data": "not-a-list"},
        [None, "model", 42, {"id": None}],
        None,
    ):
        assert generic_openai.records_from_payload(payload) == ()


def test_mistral_reader_maps_per_model_capabilities_without_provider_wide_inheritance():
    records = mistral.records_from_payload(
        {
            "data": [
                {
                    "id": "vision-chat",
                    "root": "mistral-small",
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
                {
                    "id": "future-card",
                    "capabilities": {"future_only": True},
                },
            ]
        }
    )

    assert records[0].capability.family == mc.FAMILY_CHAT
    assert records[0].capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert records[0].capability.capabilities == (mc.CAP_VISION, mc.CAP_TOOL_CALL)
    assert dict(records[0].capability.limits) == {"context_tokens": 32768}
    assert records[0].model_family == "mistral-small"
    assert records[1].capability.family == mc.FAMILY_CLASSIFICATION
    assert records[2].capability.family == mc.FAMILY_UNKNOWN
    assert records[2].capability.capabilities == ()


def test_copilot_reader_uses_picker_and_nested_supports_shape():
    records = copilot.records_from_payload(
        {
            "data": [
                {
                    "id": "picker-model",
                    "model_picker_enabled": True,
                    "capabilities": {"supports": {"tool_calls": True, "vision": True}},
                    "limits": {"max_prompt_tokens": 64000, "max_output_tokens": 8192},
                },
                {
                    "id": "utility-model",
                    "model_picker_enabled": False,
                    "capabilities": {"supports": {}},
                },
            ]
        }
    )

    assert records[0].capability.family == mc.FAMILY_CHAT
    assert records[0].capability.capabilities == (mc.CAP_TOOL_CALL, mc.CAP_VISION)
    assert dict(records[0].capability.limits) == {"input_tokens": 64000, "output_tokens": 8192}
    assert records[1].capability.family == mc.FAMILY_UNKNOWN


def test_sglang_model_info_is_structural_and_non_generation_stays_unknown():
    generation = sglang.records_from_payload(
        {
            "model_path": "org/vision-model",
            "tokenizer_path": "org/vision-model",
            "is_generation": True,
            "has_image_understanding": True,
            "has_audio_understanding": True,
            "model_type": "future_arch",
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
    assert generation.model_family == "future_arch"
    assert pooling.capability.family == mc.FAMILY_UNKNOWN


def test_identity_only_catalogs_do_not_claim_model_capability():
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
            "data": [
                {
                    "id": "MiniMax-M2-example",
                    "object": "model",
                    "owned_by": "minimax",
                }
            ],
        }
    )[0]

    assert anthropic_record.capability.family == mc.FAMILY_UNKNOWN
    assert chatgpt_record.capability.family == mc.FAMILY_UNKNOWN
    assert minimax_record.vendor == "minimax"
    assert minimax_record.capability.family == mc.FAMILY_UNKNOWN


def test_huggingface_reader_maps_pipeline_tag_as_registry_evidence():
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
    assert record.model_family == "future_vlm"


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
    assert chat.capability.modalities.input == (mc.MODALITY_TEXT,)
    assert dict(chat.capability.limits) == {"context_tokens": 131072}
    assert [control.control for control in chat.deterministic_controls] == [
        mc.CONTROL_TEMPERATURE,
        mc.CONTROL_TOP_P,
        mc.CONTROL_TOP_K,
    ]
    assert chat.raw["features"] == ["unmapped-future-feature"]
    assert ambiguous.capability.family == mc.FAMILY_UNKNOWN


def test_registry_wrapper_records_resolution_and_preserves_compatible_provider_identity():
    mistral_records = records_from_payload(
        {
            "data": [
                {
                    "id": "mistral-model",
                    "capabilities": {"completion_chat": True, "function_calling": True},
                }
            ]
        }
    )
    together_records = records_from_payload(
        [{"id": "served/model", "type": "chat", "supported_parameters": ["tools"]}],
        vendor="together",
    )

    assert mistral_records[0].vendor == "mistral"
    assert mistral_records[0].provider_schema_id == "mistral"
    assert mistral_records[0].catalog_shape_id == "mistral.models.rich.v1"
    assert mistral_records[0].provider_resolution == pcs.RESOLUTION_NATIVE_SHAPE

    assert together_records[0].vendor == "together"
    assert together_records[0].capability.family == mc.FAMILY_CHAT
    assert together_records[0].provider_schema_id == "together"
    assert together_records[0].provider_resolution == pcs.RESOLUTION_EXPLICIT


def test_reasoning_control_preserves_canonical_and_native_values():
    control = mc.ReasoningControl.build(
        mechanism="reasoning_effort",
        values=("enabled", "disabled"),
        native_values=("high", "medium", "low", "none"),
        request_path="reasoning_effort",
        response_paths=("choices[].delta.reasoning",),
        status="claimed",
        source="provider_docs_registry",
        confidence="registry",
    )

    assert control.values == (mc.REASONING_CONTROL_VALUE_ON, mc.REASONING_CONTROL_VALUE_OFF)
    assert control.native_values == ("high", "medium", "low", "none")
    assert mc.ReasoningControl.from_dict(control.to_dict()) == control


def test_model_quirks_require_structured_exact_identity_not_name_parsing():
    matching = quirks.matching_quirks(
        provider="moonshot",
        model_id="kimi-k2.5",
        model_family="",
        api_dialect=pcs.DIALECT_OPENAI_CHAT,
        capabilities=(mc.CAP_REASONING,),
    )
    lookalike = quirks.matching_quirks(
        provider="moonshot",
        model_id="proxy/kimi-k2.5-lookalike",
        model_family="",
        api_dialect=pcs.DIALECT_OPENAI_CHAT,
        capabilities=(mc.CAP_REASONING,),
    )
    opus_without_version = quirks.matching_quirks(
        provider="anthropic",
        model_family="claude-opus",
        model_id="claude-opus-4-8-in-name-only",
        api_dialect=pcs.DIALECT_ANTHROPIC_MESSAGES,
    )
    opus_structured = quirks.matching_quirks(
        provider="anthropic",
        model_family="claude-opus",
        model_version=(4, 8),
        api_dialect=pcs.DIALECT_ANTHROPIC_MESSAGES,
    )

    assert {quirk.quirk_id for quirk in matching} == {
        "moonshot.kimi-k2.5-k2.6.provider-fixed-temperature",
        "moonshot.kimi-k2.5-k2.6.tool-history-reasoning-content",
    }
    assert lookalike == ()
    assert opus_without_version == ()
    assert [quirk.quirk_id for quirk in opus_structured] == [
        "anthropic.claude-opus-4.7-plus.omit-sampling-controls"
    ]
