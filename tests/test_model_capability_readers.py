import src.model_capabilities as mc
import src.model_capability_readers as readers
from src.model_capability_readers import generic_openai, google, llamacpp, lmstudio, ollama, openai, openrouter
from src.model_capability_readers.base import (
    VENDOR_GENERIC_OPENAI,
    VENDOR_GOOGLE,
    VENDOR_LLAMACPP,
    VENDOR_LMSTUDIO,
    VENDOR_OLLAMA,
    VENDOR_OPENAI,
    VENDOR_OPENROUTER,
    detect_vendor,
    stable_model_id_for,
)


def surfaces(record):
    return set(mc.display_surfaces_for(record.capability))


def test_detect_vendor_uses_endpoint_kind_then_host_and_common_local_ports():
    assert detect_vendor("https://example.test/v1", endpoint_kind="ollama") == VENDOR_OLLAMA
    assert detect_vendor("http://127.0.0.1:8080", endpoint_kind="llama_cpp") == VENDOR_LLAMACPP
    assert detect_vendor("https://openrouter.ai/api/v1") == VENDOR_OPENROUTER
    assert detect_vendor("https://api.openai.com/v1") == VENDOR_OPENAI
    assert detect_vendor("https://generativelanguage.googleapis.com/v1beta/openai") == VENDOR_GOOGLE
    assert detect_vendor("http://127.0.0.1:11434") == VENDOR_OLLAMA
    assert detect_vendor("http://127.0.0.1:1234") == VENDOR_LMSTUDIO
    assert detect_vendor("http://127.0.0.1:8080") == VENDOR_GENERIC_OPENAI
    assert detect_vendor("http://localhost:7000/v1") == VENDOR_GENERIC_OPENAI


def test_generic_openai_reader_keeps_basic_model_payload_unknown():
    records = generic_openai.records_from_payload(
        {
            "object": "list",
            "data": [
                {"id": "gpt-example", "object": "model", "owned_by": "vendor"},
            ],
        }
    )

    assert len(records) == 1
    record = records[0]
    assert record.vendor == VENDOR_GENERIC_OPENAI
    assert record.model_id == "gpt-example"
    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.source == mc.SOURCE_PROVIDER_READER
    assert record.capability.confidence == mc.CONFIDENCE_UNKNOWN
    assert record.stable_model_id == "generic_openai|global|gpt-example"
    assert record.capability_assertions == ()
    assert record.deterministic_controls == ()
    assert surfaces(record) == set()


def test_stable_model_id_is_endpoint_scoped_for_local_or_configured_servers():
    assert stable_model_id_for("ollama", "qwen:latest", endpoint_id="7") == "ollama|endpoint:7|qwen:latest"
    assert stable_model_id_for("ollama", "qwen:latest", base_url="http://127.0.0.1:11434") != stable_model_id_for(
        "ollama",
        "qwen:latest",
        base_url="http://10.0.0.12:11434",
    )


def test_registry_uses_openai_reader_for_openai_vendor():
    records = readers.records_from_payload({"data": [{"id": "shape-only-model"}]}, vendor=VENDOR_OPENAI)

    assert len(records) == 1
    assert records[0].vendor == VENDOR_OPENAI
    assert records[0].stable_model_id == "openai|global|shape-only-model"
    assert records[0].capability.family == mc.FAMILY_UNKNOWN


def test_openai_reader_keeps_official_model_shape_identity_only():
    records = openai.records_from_payload(
        {
            "object": "list",
            "data": [
                {
                    "id": "shape-only-model",
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "openai",
                }
            ],
        }
    )

    assert len(records) == 1
    record = records[0]
    assert record.vendor == VENDOR_OPENAI
    assert record.model_id == "shape-only-model"
    assert record.capability.family == mc.FAMILY_UNKNOWN
    assert record.capability.source == mc.SOURCE_PROVIDER_READER
    assert record.capability.confidence == mc.CONFIDENCE_UNKNOWN
    assert record.capability_assertions == ()
    assert record.deterministic_controls == ()
    assert surfaces(record) == set()


def test_registry_passes_endpoint_context_to_vendor_reader():
    records = readers.records_from_payload(
        {"data": [{"id": "local.gguf", "owned_by": "llamacpp"}]},
        vendor=VENDOR_LLAMACPP,
        base_url="http://localhost:8000",
    )

    assert len(records) == 1
    assert records[0].stable_model_id == stable_model_id_for(
        VENDOR_LLAMACPP,
        "local.gguf",
        base_url="http://localhost:8000",
    )


def test_openrouter_reader_maps_rich_architecture_and_supported_parameters():
    records = openrouter.records_from_payload(
        {
            "data": [
                {
                    "id": "google/gemini-vision",
                    "name": "Gemini Vision",
                    "architecture": {"modality": "text+image->text"},
                    "supported_parameters": [
                        "tools",
                        "response_format",
                        "reasoning",
                        "include_reasoning",
                        "parallel_tool_calls",
                        "temperature",
                        "top_p",
                        "seed",
                    ],
                    "context_length": 1048576,
                    "top_provider": {"max_completion_tokens": 65536},
                },
                {
                    "id": "black-forest-labs/flux",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
                },
                {
                    "id": "vendor/image-edit-shape",
                    "architecture": {"input_modalities": ["text", "image", "file"], "output_modalities": ["text", "image"]},
                    "supported_parameters": ["structured_outputs", "web_search_options"],
                },
                {
                    "id": "vendor/audio-shape",
                    "architecture": {"input_modalities": ["text", "audio"], "output_modalities": ["text", "audio"]},
                    "supported_voices": ["alloy"],
                    "default_parameters": {"temperature": 0.7, "top_p": 0.9, "top_k": None},
                    "per_request_limits": {"prompt_tokens": 12000, "completion_tokens": 4000, "requests": "2"},
                },
                {
                    "id": "vendor/embedder",
                    "architecture": {"modality": "text->embedding"},
                },
            ]
        }
    )

    assert [record.model_id for record in records] == [
        "google/gemini-vision",
        "black-forest-labs/flux",
        "vendor/image-edit-shape",
        "vendor/audio-shape",
        "vendor/embedder",
    ]

    vision = records[0]
    assert vision.capability.family == mc.FAMILY_CHAT
    assert vision.capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert vision.capability.capabilities == (
        mc.CAP_TOOL_CALL,
        mc.CAP_JSON_MODE,
        mc.CAP_REASONING,
        mc.CAP_VISION,
    )
    assert [(assertion.capability, assertion.status) for assertion in vision.capability_assertions] == [
        (mc.CAP_TOOL_CALL, mc.ASSERTION_CLAIMED),
        (mc.CAP_JSON_MODE, mc.ASSERTION_CLAIMED),
        (mc.CAP_REASONING, mc.ASSERTION_CLAIMED),
        (mc.CAP_VISION, mc.ASSERTION_CLAIMED),
    ]
    assert [(control.control, control.status) for control in vision.deterministic_controls] == [
        (mc.CONTROL_TEMPERATURE, mc.ASSERTION_CLAIMED),
        (mc.CONTROL_TOP_P, mc.ASSERTION_CLAIMED),
        (mc.CONTROL_SEED, mc.ASSERTION_CLAIMED),
    ]
    assert dict(vision.capability.limits) == {"context_tokens": 1048576, "output_tokens": 65536}
    assert surfaces(vision) == {"chat", "vision_chat"}

    assert records[1].capability.family == mc.FAMILY_IMAGE
    assert records[1].capability.capabilities == (mc.CAP_IMAGE_GENERATION,)
    assert surfaces(records[1]) == {"image_generation"}

    image_edit = records[2]
    assert image_edit.capability.family == mc.FAMILY_IMAGE
    assert image_edit.capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE, mc.MODALITY_FILE)
    assert image_edit.capability.modalities.output == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert image_edit.capability.capabilities == (
        mc.CAP_STRUCTURED_OUTPUT,
        mc.CAP_WEB_SEARCH,
        mc.CAP_VISION,
        mc.CAP_FILES,
        mc.CAP_IMAGE_GENERATION,
        mc.CAP_IMAGE_EDITING,
    )
    assert surfaces(image_edit) == {"image_generation", "image_editing"}

    audio = records[3]
    assert audio.capability.family == mc.FAMILY_AUDIO
    assert audio.capability.capabilities == (mc.CAP_AUDIO_INPUT, mc.CAP_AUDIO_OUTPUT, mc.CAP_TTS)
    assert dict(audio.capability.limits) == {
        "per_request_completion_tokens": 4000,
        "per_request_prompt_tokens": 12000,
        "per_request_requests": 2,
    }
    assert [control.control for control in audio.deterministic_controls] == [
        mc.CONTROL_TEMPERATURE,
        mc.CONTROL_TOP_P,
    ]
    assert surfaces(audio) == {"audio_realtime"}

    assert records[4].capability.family == mc.FAMILY_EMBEDDING
    assert surfaces(records[4]) == {"embeddings"}


def test_google_reader_maps_provider_fields_without_claiming_unreported_modalities():
    records = google.records_from_payload(
        {
            "models": [
                {
                    "name": "models/gemini-3.1-flash-image",
                    "displayName": "Gemini 3.1 Flash Image",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 1000000,
                    "outputTokenLimit": 8192,
                    "thinking": True,
                    "temperature": 1.0,
                    "topP": 0.95,
                    "topK": 40,
                },
                {
                    "name": "models/text-embedding-example",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }
    )

    assert len(records) == 2
    content = records[0]
    assert content.vendor == VENDOR_GOOGLE
    assert content.model_id == "gemini-3.1-flash-image"
    assert content.capability.family == mc.FAMILY_UNKNOWN
    assert content.capability.modalities.input == ()
    assert content.capability.modalities.output == ()
    assert content.capability.capabilities == (mc.CAP_REASONING,)
    assert dict(content.capability.limits) == {
        "context_tokens": 1000000,
        "input_tokens": 1000000,
        "output_tokens": 8192,
    }
    assert [control.control for control in content.deterministic_controls] == [
        mc.CONTROL_TEMPERATURE,
        mc.CONTROL_TOP_P,
        mc.CONTROL_TOP_K,
    ]
    assert surfaces(content) == set()

    embedding = records[1]
    assert embedding.capability.family == mc.FAMILY_EMBEDDING
    assert surfaces(embedding) == {"embeddings"}


def test_google_ai_studio_mapping_does_not_infer_media_from_model_names():
    records = google.records_from_payload(
        {
            "models": [
                {
                    "name": "models/imagen-4.0-generate-001",
                    "displayName": "Imagen 4",
                    "supportedGenerationMethods": ["predict"],
                },
                {
                    "name": "models/veo-3.1-generate-preview",
                    "displayName": "Veo 3.1",
                    "supportedGenerationMethods": ["predictLongRunning"],
                },
                {
                    "name": "models/gemini-3.1-flash-tts-preview",
                    "supportedGenerationMethods": ["generateContent", "countTokens", "createCachedContent", "batchGenerateContent"],
                },
                {
                    "name": "models/lyria-3-pro-preview",
                    "displayName": "Lyria 3 Pro Preview",
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
            ]
        }
    )

    assert len(records) == 4
    assert [record.capability.family for record in records] == [
        mc.FAMILY_UNKNOWN,
        mc.FAMILY_UNKNOWN,
        mc.FAMILY_UNKNOWN,
        mc.FAMILY_UNKNOWN,
    ]
    assert all(record.capability.modalities.input == () for record in records)
    assert all(record.capability.modalities.output == () for record in records)
    assert all(surfaces(record) == set() for record in records)
    assert [control.control for control in records[2].deterministic_controls] == [
        mc.CONTROL_PROMPT_CACHING,
        mc.CONTROL_BATCH,
    ]


def test_google_ai_studio_mapping_keeps_unrecognized_predict_models_unknown():
    records = google.records_from_payload(
        {
            "models": [
                {
                    "name": "models/vendor-future-media-001",
                    "supportedGenerationMethods": ["predict"],
                }
            ]
        }
    )

    assert len(records) == 1
    assert records[0].capability.family == mc.FAMILY_UNKNOWN
    assert surfaces(records[0]) == set()


def test_ollama_reader_maps_show_capabilities_and_tags_are_unknown():
    vision = ollama.record_from_show_payload(
        "llava:latest",
        {
            "capabilities": ["completion", "vision", "tools"],
            "model_info": {"llama.context_length": 4096},
        },
    )
    embedding = ollama.record_from_show_payload(
        "nomic-embed-text:latest",
        {"capabilities": ["embedding"]},
    )
    tags = ollama.records_from_tags_payload({"models": [{"name": "qwen3:latest"}]})

    assert vision is not None
    assert vision.capability.family == mc.FAMILY_CHAT
    assert vision.capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert vision.capability.capabilities == (mc.CAP_VISION, mc.CAP_TOOL_CALL)
    assert dict(vision.capability.limits) == {"context_tokens": 4096}
    assert surfaces(vision) == {"chat", "vision_chat"}

    assert embedding is not None
    assert embedding.capability.family == mc.FAMILY_EMBEDDING
    assert surfaces(embedding) == {"embeddings"}

    assert len(tags) == 1
    assert tags[0].capability.family == mc.FAMILY_UNKNOWN
    assert surfaces(tags[0]) == set()


def test_ollama_reader_uses_show_shape_without_architecture_name_matching():
    record = ollama.record_from_show_payload(
        "local:latest",
        {
            "capabilities": ["completion", "thinking", "tools"],
            "parameters": "temperature 0.7\nnum_ctx 8192",
            "model_info": {
                "future_architecture.context_length": 32768,
                "future_architecture.embedding_length": 4096,
            },
        },
    )

    assert record is not None
    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.modalities.input == (mc.MODALITY_TEXT,)
    assert record.capability.modalities.output == (mc.MODALITY_TEXT,)
    assert record.capability.capabilities == (mc.CAP_REASONING, mc.CAP_TOOL_CALL)
    assert dict(record.capability.limits) == {"context_tokens": 8192}
    assert surfaces(record) == {"chat"}


def test_ollama_reader_uses_generic_model_info_context_length_when_no_num_ctx():
    record = ollama.record_from_show_payload(
        "local:latest",
        {
            "capabilities": ["completion"],
            "model_info": {"future_architecture.context_length": 32768},
        },
    )

    assert record is not None
    assert record.capability.family == mc.FAMILY_CHAT
    assert dict(record.capability.limits) == {"context_tokens": 32768}


def test_lmstudio_reader_uses_native_v1_capabilities_when_present():
    records = lmstudio.records_from_payload(
        {
            "models": [
                {
                    "type": "llm",
                    "key": "google/gemma-vl",
                    "display_name": "Gemma VL",
                    "capabilities": {
                        "vision": True,
                        "trained_for_tool_use": True,
                        "reasoning": {"allowed_options": ["off", "on"], "default": "on"},
                    },
                    "loaded_instances": [
                        {"config": {"context_length": 8192}},
                        {"config": {"context_length": 4096}},
                    ],
                    "max_context_length": 262144,
                },
                {
                    "type": "embedding",
                    "key": "nomic/embed",
                },
                {"key": "shape-without-type"},
            ]
        }
    )

    assert len(records) == 3
    vision = records[0]
    assert vision.vendor == VENDOR_LMSTUDIO
    assert vision.model_id == "google/gemma-vl"
    assert vision.display_name == "Gemma VL"
    assert vision.capability.family == mc.FAMILY_CHAT
    assert vision.capability.modalities.input == (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
    assert vision.capability.capabilities == (mc.CAP_VISION, mc.CAP_TOOL_CALL, mc.CAP_REASONING)
    assert dict(vision.capability.limits) == {"context_tokens": 4096, "max_context_tokens": 262144}
    assert surfaces(vision) == {"chat", "vision_chat"}

    assert records[1].capability.family == mc.FAMILY_EMBEDDING
    assert surfaces(records[1]) == {"embeddings"}

    assert records[2].capability.family == mc.FAMILY_UNKNOWN
    assert surfaces(records[2]) == set()


def test_lmstudio_reader_uses_legacy_native_v0_shape_for_family_and_limits():
    records = lmstudio.records_from_payload(
        {
            "data": [
                {
                    "id": "local-gemma",
                    "type": "llm",
                    "arch": "gemma3",
                    "loaded_context_length": 16384,
                    "max_context_length": 32768,
                },
                {
                    "id": "text-embedding-local",
                    "type": "embeddings",
                    "max_context_length": 2048,
                },
            ]
        }
    )

    assert len(records) == 2
    chat = records[0]
    assert chat.vendor == VENDOR_LMSTUDIO
    assert chat.capability.family == mc.FAMILY_CHAT
    assert chat.capability.modalities.input == (mc.MODALITY_TEXT,)
    assert chat.capability.capabilities == ()
    assert dict(chat.capability.limits) == {"context_tokens": 16384, "max_context_tokens": 32768}
    assert surfaces(chat) == {"chat"}

    assert records[1].capability.family == mc.FAMILY_EMBEDDING
    assert dict(records[1].capability.limits) == {"context_tokens": 2048}
    assert surfaces(records[1]) == {"embeddings"}


def test_lmstudio_openai_compatible_model_list_remains_identity_only():
    records = lmstudio.records_from_payload(
        {
            "object": "list",
            "data": [
                {"id": "local-gemma-3-270m-it-qat-q4_k_m", "object": "model", "owned_by": "organization_owner"},
                {"id": "text-embedding-nomic-embed-text-v1.5", "object": "model", "owned_by": "organization_owner"},
            ],
        }
    )

    assert len(records) == 2
    for record in records:
        assert record.vendor == VENDOR_LMSTUDIO
        assert record.capability.family == mc.FAMILY_UNKNOWN
        assert record.capability_assertions == ()
        assert surfaces(record) == set()


def test_lmstudio_unexpected_native_endpoint_error_yields_no_records():
    assert lmstudio.records_from_payload({"error": "Unexpected endpoint or method. (GET /api/v1/models)"}) == ()


def test_llamacpp_reader_merges_models_props_and_slots_payloads():
    models_payload = {
        "object": "list",
        "data": [
            {
                "id": "gemma-4-E2B-it-Q8_0.gguf",
                "owned_by": "llamacpp",
                "meta": {
                    "n_ctx_train": 131072,
                    "n_params": 4647450147,
                    "size": 5032532108,
                },
            }
        ],
        "models": [
            {
                "name": "gemma-4-E2B-it-Q8_0.gguf",
                "model": "gemma-4-E2B-it-Q8_0.gguf",
                "capabilities": ["completion"],
                "details": {"format": "gguf"},
            }
        ],
    }
    props_payload = {
        "model_alias": "gemma-4-E2B-it-Q8_0.gguf",
        "model_path": "/models/gemma-4-E2B-it-Q8_0.gguf",
        "build_info": "b1-c8ac02f",
        "total_slots": 4,
        "modalities": {"vision": False, "audio": False},
        "chat_template_caps": {
            "supports_object_arguments": True,
            "supports_parallel_tool_calls": True,
            "supports_preserve_reasoning": False,
            "supports_string_content": True,
            "supports_system_role": True,
            "supports_tool_calls": True,
            "supports_tools": True,
            "supports_typed_content": False,
        },
        "default_generation_settings": {
            "n_ctx": 16384,
            "params": {
                "temperature": 1.0,
                "top_p": 0.95,
                "seed": 4294967295,
                "stream": True,
                "samplers": ["top_p", "temperature"],
            },
        },
    }
    slots_payload = [
        {"id": 0, "n_ctx": 16384, "speculative": False, "is_processing": False},
        {"id": 1, "n_ctx": 16384, "speculative": False, "is_processing": False},
        {"id": 2, "n_ctx": 16384, "speculative": False, "is_processing": False},
        {"id": 3, "n_ctx": 16384, "speculative": False, "is_processing": False},
    ]

    records = llamacpp.records_from_payloads(
        models_payload=models_payload,
        props_payload=props_payload,
        slots_payload=slots_payload,
        base_url="http://localhost:8000",
    )

    assert len(records) == 1
    record = records[0]
    assert record.vendor == VENDOR_LLAMACPP
    assert record.model_id == "gemma-4-E2B-it-Q8_0.gguf"
    assert record.stable_model_id == stable_model_id_for(
        VENDOR_LLAMACPP,
        "gemma-4-E2B-it-Q8_0.gguf",
        base_url="http://localhost:8000",
    )
    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.modalities.input == (mc.MODALITY_TEXT,)
    assert record.capability.modalities.output == (mc.MODALITY_TEXT,)
    assert record.capability.capabilities == (mc.CAP_TOOL_CALL, mc.CAP_STREAMING)
    assert dict(record.capability.limits) == {
        "context_tokens": 16384,
        "model_bytes": 5032532108,
        "parallel_slots": 4,
        "parameters": 4647450147,
        "training_context_tokens": 131072,
    }
    assert surfaces(record) == {"chat"}

    assertion_status = {assertion.capability: assertion.status for assertion in record.capability_assertions}
    assert assertion_status[mc.CAP_TOOL_CALL] == mc.ASSERTION_CLAIMED
    assert assertion_status[mc.CAP_STREAMING] == mc.ASSERTION_CLAIMED
    assert assertion_status[mc.CAP_VISION] == mc.ASSERTION_UNSUPPORTED
    assert assertion_status[mc.CAP_AUDIO_INPUT] == mc.ASSERTION_UNSUPPORTED
    assert mc.CAP_REASONING not in assertion_status

    controls = {control.control: control.status for control in record.deterministic_controls}
    assert controls == {
        mc.CONTROL_TEMPERATURE: mc.ASSERTION_CLAIMED,
        mc.CONTROL_TOP_P: mc.ASSERTION_CLAIMED,
        mc.CONTROL_SEED: mc.ASSERTION_CLAIMED,
        mc.CONTROL_SYSTEM_PROMPT: mc.ASSERTION_CLAIMED,
        mc.CONTROL_TOOL_CHOICE: mc.ASSERTION_CLAIMED,
    }


def test_llamacpp_openai_model_list_without_native_capability_shape_stays_unknown():
    records = llamacpp.records_from_payload(
        {
            "object": "list",
            "data": [
                {
                    "id": "local-chat.gguf",
                    "owned_by": "llamacpp",
                }
            ],
        }
    )

    assert len(records) == 1
    assert records[0].capability.family == mc.FAMILY_UNKNOWN
    assert records[0].capability.capabilities == ()
    assert records[0].capability_assertions == ()
    assert surfaces(records[0]) == set()


def test_llamacpp_props_payload_reports_unsupported_modalities_without_model_list():
    records = llamacpp.records_from_payload(
        {
            "model_alias": "local.gguf",
            "modalities": {"vision": False, "audio": False},
            "chat_template_caps": {"supports_tools": False, "supports_preserve_reasoning": False},
            "default_generation_settings": {"n_ctx": 4096, "params": {"stream": True}},
        }
    )

    assert len(records) == 1
    record = records[0]
    assert record.capability.family == mc.FAMILY_CHAT
    assert record.capability.capabilities == (mc.CAP_STREAMING,)
    assert {a.capability: a.status for a in record.capability_assertions} == {
        mc.CAP_STREAMING: mc.ASSERTION_CLAIMED,
        mc.CAP_VISION: mc.ASSERTION_UNSUPPORTED,
        mc.CAP_AUDIO_INPUT: mc.ASSERTION_UNSUPPORTED,
    }
