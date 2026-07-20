import src.model_capabilities as mc


def surfaces(capability):
    return set(mc.display_surfaces_for(capability))


def test_endpoint_type_llm_maps_to_explicit_chat_capability():
    capability = mc.capability_from_endpoint_type("llm")

    assert capability.family == mc.FAMILY_CHAT
    assert capability.primary_task == mc.TASK_CHAT_COMPLETIONS
    assert capability.modalities.input == (mc.MODALITY_TEXT,)
    assert capability.modalities.output == (mc.MODALITY_TEXT,)
    assert capability.source == mc.SOURCE_ENDPOINT_CONFIG
    assert capability.confidence == mc.CONFIDENCE_EXPLICIT
    assert surfaces(capability) == {"chat"}


def test_endpoint_type_image_maps_to_explicit_image_generation_capability():
    capability = mc.capability_from_endpoint_type("image")

    assert capability.family == mc.FAMILY_IMAGE
    assert capability.primary_task == mc.TASK_IMAGE_GENERATE
    assert capability.modalities.output == (mc.MODALITY_IMAGE,)
    assert capability.capabilities == (mc.CAP_IMAGE_GENERATION,)
    assert capability.source == mc.SOURCE_ENDPOINT_CONFIG
    assert capability.confidence == mc.CONFIDENCE_EXPLICIT
    assert surfaces(capability) == {"image_generation"}


def test_missing_or_unknown_endpoint_type_does_not_imply_chat():
    for model_type in (None, "", "openai-compatible", "text"):
        capability = mc.capability_from_endpoint_type(model_type)

        assert capability.family == mc.FAMILY_UNKNOWN
        assert capability.primary_task == mc.TASK_UNKNOWN
        assert capability.source == mc.SOURCE_ENDPOINT_CONFIG
        assert mc.display_surfaces_for(capability) == ()


def test_provider_record_normalizes_aliases_and_boolean_capability_maps():
    capability = mc.ModelCapability.from_dict(
        {
            "family": "llm",
            "modalities": {
                "input": ["text", "images", "docs", "images"],
                "output": "text",
            },
            "capabilities": {
                "tools": True,
                "unknown_vendor_flag": True,
                "vision": True,
                "tts": False,
            },
            "limits": {"max_context_tokens": 32768, "": "ignored"},
            "source": "provider_reader",
            "confidence": "provider_reported",
        }
    )

    assert capability.family == mc.FAMILY_CHAT
    assert capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE, mc.MODALITY_FILE)
    assert capability.modalities.output == (mc.MODALITY_TEXT,)
    assert capability.capabilities == (mc.CAP_TOOL_CALL, mc.CAP_VISION)
    assert capability.limits == (("max_context_tokens", 32768),)
    assert surfaces(capability) == {"chat", "vision_chat", "document_chat"}
    assert capability.to_dict() == {
        "family": mc.FAMILY_CHAT,
        "primary_task": mc.TASK_CHAT_COMPLETIONS,
        "modalities": {
            "input": [mc.MODALITY_TEXT, mc.MODALITY_IMAGE, mc.MODALITY_FILE],
            "output": [mc.MODALITY_TEXT],
        },
        "capabilities": [mc.CAP_TOOL_CALL, mc.CAP_VISION],
        "limits": {"max_context_tokens": 32768},
        "source": mc.SOURCE_PROVIDER_READER,
        "confidence": mc.CONFIDENCE_PROVIDER_REPORTED,
    }


def test_unknown_or_malformed_capability_record_stays_unknown():
    assert mc.ModelCapability.from_dict(None).to_dict() == mc.unknown_capability().to_dict()

    capability = mc.ModelCapability.build(
        family="not-real",
        primary_task=1234,
        input_modalities=object(),
        output_modalities=["text", "not-real"],
        capabilities=["vision", "not-real"],
        source="not-real",
        confidence="not-real",
    )

    assert capability.family == mc.FAMILY_UNKNOWN
    assert capability.primary_task == "1234"
    assert capability.modalities.input == ()
    assert capability.modalities.output == (mc.MODALITY_TEXT,)
    assert capability.capabilities == (mc.CAP_VISION,)
    assert capability.source == mc.SOURCE_UNKNOWN
    assert capability.confidence == mc.CONFIDENCE_UNKNOWN
    assert mc.display_surfaces_for(capability) == ()


def test_display_surface_queries_cover_core_model_categories():
    assert surfaces(
        mc.ModelCapability.build(
            family=mc.FAMILY_IMAGE,
            input_modalities=[mc.MODALITY_IMAGE],
            output_modalities=[mc.MODALITY_IMAGE],
            capabilities=[mc.CAP_INPAINTING],
        )
    ) == {"image_editing"}

    assert surfaces(mc.ModelCapability.build(family=mc.FAMILY_EMBEDDING)) == {"embeddings"}
    assert surfaces(mc.ModelCapability.build(family=mc.FAMILY_RERANK)) == {"rerank_scoring"}
    assert surfaces(mc.ModelCapability.build(family=mc.FAMILY_MODERATION)) == {"moderation_classification"}
    assert surfaces(mc.ModelCapability.build(family=mc.FAMILY_CLASSIFICATION)) == {"moderation_classification"}


def test_audio_surface_matches_audio_input_or_output_when_capability_is_known():
    transcription = mc.ModelCapability.build(
        family=mc.FAMILY_AUDIO,
        primary_task=mc.TASK_AUDIO_TRANSCRIBE,
        input_modalities=[mc.MODALITY_AUDIO],
        output_modalities=[mc.MODALITY_TEXT],
        capabilities=[mc.CAP_TRANSCRIPTION],
    )
    synthesis = mc.ModelCapability.build(
        family=mc.FAMILY_AUDIO,
        primary_task=mc.TASK_AUDIO_SYNTHESIZE,
        input_modalities=[mc.MODALITY_TEXT],
        output_modalities=[mc.MODALITY_AUDIO],
        capabilities=[mc.CAP_TTS],
    )

    assert surfaces(transcription) == {"audio_realtime"}
    assert surfaces(synthesis) == {"audio_realtime"}


def test_capability_assertion_tracks_claimed_status_separately_from_capability_metadata():
    assertion = mc.CapabilityAssertion.build(
        capability="tools",
        status="claimed",
        source="provider_reader",
        confidence="provider_reported",
        evidence={"field": "supported_parameters"},
    )

    assert assertion.capability == mc.CAP_TOOL_CALL
    assert assertion.status == mc.ASSERTION_CLAIMED
    assert assertion.source == mc.SOURCE_PROVIDER_READER
    assert assertion.confidence == mc.CONFIDENCE_PROVIDER_REPORTED
    assert assertion.to_dict() == {
        "capability": mc.CAP_TOOL_CALL,
        "status": mc.ASSERTION_CLAIMED,
        "source": mc.SOURCE_PROVIDER_READER,
        "confidence": mc.CONFIDENCE_PROVIDER_REPORTED,
        "evidence": {"field": "supported_parameters"},
        "tested_at": "",
    }


def test_capability_probe_result_converts_pass_and_fail_to_assertions():
    passed = mc.CapabilityProbeResult.build(
        provider="openrouter",
        endpoint_id="ep-1",
        model_id="vendor/model",
        stable_model_id="openrouter|endpoint:ep-1|vendor/model",
        capability="tool_calls",
        status="pass",
        tested_at="2026-06-04T20:00:00Z",
        request_hash="abc123",
        response_fingerprint="fp-test",
        evidence={"contract": "single_fake_tool"},
    )
    failed = mc.CapabilityProbeResult.build(
        provider="openrouter",
        model_id="vendor/model",
        capability="vision",
        status="fail",
    )

    pass_assertion = passed.to_assertion()
    fail_assertion = failed.to_assertion()

    assert pass_assertion.capability == mc.CAP_TOOL_CALL
    assert pass_assertion.status == mc.ASSERTION_VERIFIED
    assert pass_assertion.source == mc.SOURCE_CAPABILITY_PROBE
    assert pass_assertion.confidence == mc.CONFIDENCE_EXPLICIT
    assert pass_assertion.tested_at == "2026-06-04T20:00:00Z"
    assert dict(pass_assertion.evidence)["request_hash"] == "abc123"
    assert dict(pass_assertion.evidence)["contract"] == "single_fake_tool"

    assert fail_assertion.capability == mc.CAP_VISION
    assert fail_assertion.status == mc.ASSERTION_UNSUPPORTED
    assert fail_assertion.source == mc.SOURCE_CAPABILITY_PROBE


def test_deterministic_controls_are_normalized_as_claims_not_capabilities():
    controls = mc.deterministic_controls_from_values(
        ["temp", "top-p", "top-k", "seed", "unknown"],
        source=mc.SOURCE_PROVIDER_READER,
    )

    assert [control.control for control in controls] == [
        mc.CONTROL_TEMPERATURE,
        mc.CONTROL_TOP_P,
        mc.CONTROL_TOP_K,
        mc.CONTROL_SEED,
    ]
    assert {control.status for control in controls} == {mc.ASSERTION_CLAIMED}
    assert {control.source for control in controls} == {mc.SOURCE_PROVIDER_READER}


def test_reasoning_control_mechanisms_normalize_known_provider_shapes():
    values = [
        "think_directive",
        "system_prompt_directive",
        "enable_thinking",
        "think_bool",
        "reasoning_object",
        "thinking_budget",
        "reasoning_effort",
    ]

    assert [mc.normalize_reasoning_control_mechanism(value) for value in values] == [
        mc.REASONING_CONTROL_MESSAGE_DIRECTIVE,
        mc.REASONING_CONTROL_SYSTEM_DIRECTIVE,
        mc.REASONING_CONTROL_TEMPLATE_KWARG,
        mc.REASONING_CONTROL_NATIVE_BOOL,
        mc.REASONING_CONTROL_STRUCTURED_OBJECT,
        mc.REASONING_CONTROL_BUDGET,
        mc.REASONING_CONTROL_EFFORT,
    ]


def test_reasoning_control_values_can_describe_provider_supported_auto():
    values = ["enabled", "disabled", "adaptive", "dynamic", "provider_auto"]

    assert [mc.normalize_reasoning_control_value(value) for value in values] == [
        mc.REASONING_CONTROL_VALUE_ON,
        mc.REASONING_CONTROL_VALUE_OFF,
        mc.REASONING_CONTROL_VALUE_AUTO,
        mc.REASONING_CONTROL_VALUE_AUTO,
        mc.REASONING_CONTROL_VALUE_AUTO,
    ]
    assert mc.normalize_reasoning_control_value("message_directive") == ""
