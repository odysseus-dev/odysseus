"""Canonical serving-provider and payload-shape metadata.

This module describes provider/API dialects and model-catalog JSON shapes.  It
does not perform network I/O, parse model names, or claim that every model on a
provider supports every feature exposed by that provider.  Model capability is
still read from each model record (or remains unknown).

Resolution is deliberately stepped and deterministic:

1. an explicit provider/endpoint kind;
2. an exact known provider host;
3. a discriminating native payload shape;
4. a general structural model-list shape;
5. unknown.

Unknown keys are left to the reader's ``raw`` evidence.  They never become a
capability merely because a future provider happens to add them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


PROVIDER_UNKNOWN = "unknown"
PROVIDER_GENERIC_OPENAI = "generic_openai"

DIALECT_OPENAI_CHAT = "openai_chat_completions"
DIALECT_OPENAI_RESPONSES = "openai_responses"
DIALECT_ANTHROPIC_MESSAGES = "anthropic_messages"
DIALECT_COHERE_V2 = "cohere_v2"
DIALECT_GOOGLE_GENERATE_CONTENT = "google_generate_content"
DIALECT_OLLAMA_NATIVE = "ollama_native"
DIALECT_LMSTUDIO_NATIVE_V1 = "lmstudio_native_v1"
DIALECT_LLAMACPP_NATIVE = "llamacpp_native"
DIALECT_SGLANG_NATIVE = "sglang_native"
DIALECT_HUGGINGFACE_HUB = "huggingface_hub"
DIALECT_CHATGPT_SUBSCRIPTION = "chatgpt_subscription_responses"

RESOLUTION_EXPLICIT = "explicit"
RESOLUTION_ENDPOINT_KIND = "endpoint_kind"
RESOLUTION_HOST = "host"
RESOLUTION_NATIVE_SHAPE = "native_shape"
RESOLUTION_GENERAL_SHAPE = "general_shape"
RESOLUTION_UNKNOWN = "unknown"

ENVELOPE_DATA = "data"
ENVELOPE_MODELS = "models"
ENVELOPE_BARE_LIST = "bare_list"
ENVELOPE_SINGLE = "single"

_MISSING = object()


def _token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _path_present(value: Any, path: str) -> bool:
    return _path_value(value, path) is not _MISSING


def _items_for_envelope(payload: Any, envelope: str) -> tuple[Mapping[str, Any], ...]:
    if envelope == ENVELOPE_BARE_LIST:
        values = payload if isinstance(payload, (list, tuple)) else ()
    elif envelope == ENVELOPE_SINGLE:
        values = (payload,) if isinstance(payload, Mapping) else ()
    elif isinstance(payload, Mapping):
        values = payload.get(envelope)
        values = values if isinstance(values, (list, tuple)) else ()
    else:
        values = ()
    return tuple(item for item in values if isinstance(item, Mapping))


@dataclass(frozen=True)
class ProviderCatalogShape:
    """A declarative, versioned provider model-catalog shape."""

    shape_id: str
    provider_id: str
    endpoint_path: str
    envelope: str
    identity_paths: tuple[str, ...]
    required_root_paths: tuple[str, ...] = ()
    required_item_paths: tuple[str, ...] = ()
    required_item_any_paths: tuple[str, ...] = ()
    item_types: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    item_values: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    capability_paths: tuple[str, ...] = ()
    api_version: str = ""
    priority: int = 0
    latest: bool = True

    def items(self, payload: Any) -> tuple[Mapping[str, Any], ...]:
        return _items_for_envelope(payload, self.envelope)

    def matches(self, payload: Any) -> bool:
        if self.required_root_paths:
            if not isinstance(payload, Mapping):
                return False
            if not all(_path_present(payload, path) for path in self.required_root_paths):
                return False

        items = self.items(payload)
        if not items:
            return False
        for item in items:
            if self.identity_paths:
                has_identity = False
                for path in self.identity_paths:
                    value = _path_value(item, path)
                    if value is not _MISSING and value is not None and value != "":
                        has_identity = True
                        break
                if not has_identity:
                    continue
            if not all(_path_present(item, path) for path in self.required_item_paths):
                continue
            if self.required_item_any_paths and not any(
                _path_present(item, path) for path in self.required_item_any_paths
            ):
                continue
            if any(
                not isinstance(_path_value(item, path), expected_types)
                for path, expected_types in self.item_types
            ):
                continue
            if any(_path_value(item, path) not in expected for path, expected in self.item_values):
                continue
            return True
        return False


@dataclass(frozen=True)
class ProviderApiShape:
    """Stable request/response field paths for one API dialect.

    Paths are documentation and validation inputs, not late response parsers.
    A model-specific exception can narrow these fields in the model quirk
    registry without changing the provider's general transport contract.
    """

    dialect: str
    request_path: str
    stream_path: str = ""
    model_field: str = "model"
    message_field: str = "messages"
    tool_request_paths: tuple[str, ...] = ()
    tool_response_paths: tuple[str, ...] = ()
    text_response_paths: tuple[str, ...] = ()
    reasoning_response_paths: tuple[str, ...] = ()
    request_control_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderCapabilitySchema:
    provider_id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    host_suffixes: tuple[str, ...] = ()
    api_shapes: tuple[ProviderApiShape, ...] = ()
    catalog_shapes: tuple[ProviderCatalogShape, ...] = ()
    fallback_provider_id: str = PROVIDER_GENERIC_OPENAI
    model_capabilities_are_per_model: bool = True


@dataclass(frozen=True)
class ProviderResolution:
    provider_id: str
    stage: str
    schema: ProviderCapabilitySchema
    catalog_shape: ProviderCatalogShape | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "stage": self.stage,
            "schema_id": self.schema.provider_id,
            "catalog_shape_id": self.catalog_shape.shape_id if self.catalog_shape else "",
        }


OPENAI_CHAT_SHAPE = ProviderApiShape(
    dialect=DIALECT_OPENAI_CHAT,
    request_path="/v1/chat/completions",
    stream_path="/v1/chat/completions",
    tool_request_paths=("tools[].function", "tool_choice", "parallel_tool_calls"),
    tool_response_paths=("choices[].message.tool_calls[].function", "choices[].delta.tool_calls[].function"),
    text_response_paths=("choices[].message.content", "choices[].delta.content"),
    reasoning_response_paths=(
        "choices[].message.reasoning_content",
        "choices[].delta.reasoning_content",
        "choices[].delta.reasoning",
        "choices[].delta.thinking",
    ),
    request_control_paths=(
        "temperature",
        "top_p",
        "seed",
        "response_format",
        "reasoning_effort",
    ),
)

OPENAI_RESPONSES_SHAPE = ProviderApiShape(
    dialect=DIALECT_OPENAI_RESPONSES,
    request_path="/v1/responses",
    stream_path="/v1/responses",
    message_field="input",
    tool_request_paths=("tools[]", "tool_choice", "parallel_tool_calls"),
    tool_response_paths=(
        "output[type=function_call].arguments",
        "response.function_call_arguments.delta",
    ),
    text_response_paths=(
        "output[type=message].content[type=output_text].text",
        "response.output_text.delta",
    ),
    reasoning_response_paths=(
        "output[type=reasoning].summary[].text",
        "output[type=reasoning].encrypted_content",
        "response.reasoning_summary_text.delta",
    ),
    request_control_paths=("temperature", "top_p", "reasoning", "text.format"),
)

ANTHROPIC_MESSAGES_SHAPE = ProviderApiShape(
    dialect=DIALECT_ANTHROPIC_MESSAGES,
    request_path="/v1/messages",
    stream_path="/v1/messages",
    tool_request_paths=("tools[].input_schema", "tool_choice", "messages[].content[].tool_result"),
    tool_response_paths=("content[].tool_use", "content_block_delta.delta.partial_json"),
    text_response_paths=("content[].text", "content_block_delta.delta.text"),
    reasoning_response_paths=("content[].thinking", "content[].signature"),
    request_control_paths=("temperature", "top_p", "top_k", "thinking", "output_config"),
)

COHERE_V2_SHAPE = ProviderApiShape(
    dialect=DIALECT_COHERE_V2,
    request_path="/v2/chat",
    stream_path="/v2/chat",
    tool_request_paths=("tools[].parameters", "messages[].tool_calls", "messages[].tool_results"),
    tool_response_paths=("message.tool_calls", "tool-call-start", "tool-call-delta", "tool-call-end"),
    text_response_paths=("message.content[].text", "content-delta.delta.message.content.text"),
    reasoning_response_paths=("message.content[].thinking",),
    request_control_paths=(
        "temperature",
        "p",
        "k",
        "seed",
        "response_format",
        "thinking",
    ),
)

GOOGLE_CONTENT_SHAPE = ProviderApiShape(
    dialect=DIALECT_GOOGLE_GENERATE_CONTENT,
    request_path="/v1beta/models/{model}:generateContent",
    stream_path="/v1beta/models/{model}:streamGenerateContent?alt=sse",
    message_field="contents",
    tool_request_paths=("tools[].functionDeclarations", "toolConfig"),
    tool_response_paths=("candidates[].content.parts[].functionCall", "candidates[].content.parts[].functionResponse"),
    text_response_paths=("candidates[].content.parts[].text",),
    reasoning_response_paths=("candidates[].content.parts[].thought", "candidates[].content.parts[].thoughtSignature"),
    request_control_paths=(
        "generationConfig.temperature",
        "generationConfig.topP",
        "generationConfig.topK",
        "generationConfig.thinkingConfig",
        "generationConfig.responseJsonSchema",
    ),
)

OLLAMA_NATIVE_SHAPE = ProviderApiShape(
    dialect=DIALECT_OLLAMA_NATIVE,
    request_path="/api/chat",
    stream_path="/api/chat",
    tool_request_paths=("tools[].function", "messages[].tool_calls[].function"),
    tool_response_paths=("message.tool_calls[].function",),
    text_response_paths=("message.content",),
    reasoning_response_paths=("message.thinking",),
    request_control_paths=("think", "format", "options.temperature", "options.top_p", "options.seed"),
)

LMSTUDIO_NATIVE_SHAPE = ProviderApiShape(
    dialect=DIALECT_LMSTUDIO_NATIVE_V1,
    request_path="/api/v1/chat",
    stream_path="/api/v1/chat",
    message_field="input",
    tool_request_paths=("integrations[].mcp",),
    text_response_paths=("output[].content",),
    reasoning_response_paths=("output[].reasoning",),
    request_control_paths=("temperature", "top_p", "reasoning", "response_format"),
)

SGLANG_NATIVE_SHAPE = ProviderApiShape(
    dialect=DIALECT_SGLANG_NATIVE,
    request_path="/generate",
    stream_path="/generate",
    message_field="text",
    text_response_paths=("text",),
    request_control_paths=("sampling_params.temperature", "sampling_params.top_p", "sampling_params.seed"),
)

CHATGPT_SUBSCRIPTION_SHAPE = ProviderApiShape(
    dialect=DIALECT_CHATGPT_SUBSCRIPTION,
    request_path="/backend-api/codex/responses",
    stream_path="/backend-api/codex/responses",
    message_field="input",
    tool_request_paths=("tools[]", "input[].function_call_output"),
    tool_response_paths=("response.function_call_arguments.delta", "response.output_item.done"),
    text_response_paths=("response.output_text.delta",),
    reasoning_response_paths=("response.reasoning_summary_text.delta", "reasoning.encrypted_content"),
    request_control_paths=("reasoning", "text", "parallel_tool_calls"),
)


GENERAL_DATA_SHAPE = ProviderCatalogShape(
    shape_id="openai-compatible.models.data.v1",
    provider_id=PROVIDER_GENERIC_OPENAI,
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id", "name", "model"),
)
GENERAL_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="general.models-envelope.v1",
    provider_id=PROVIDER_GENERIC_OPENAI,
    endpoint_path="/models",
    envelope=ENVELOPE_MODELS,
    identity_paths=("id", "key", "slug", "name", "model"),
)
GENERAL_BARE_SHAPE = ProviderCatalogShape(
    shape_id="openai-compatible.models.bare-list.v1",
    provider_id=PROVIDER_GENERIC_OPENAI,
    endpoint_path="/models",
    envelope=ENVELOPE_BARE_LIST,
    identity_paths=("id", "name", "model"),
)

OPENAI_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="openai.models.identity.v1",
    provider_id="openai",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("object", "created", "owned_by"),
    item_values=(("object", ("model",)),),
    api_version="v1",
)
OPENROUTER_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="openrouter.models.rich.v1",
    provider_id="openrouter",
    endpoint_path="/api/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_any_paths=("architecture", "supported_parameters", "top_provider", "canonical_slug"),
    capability_paths=(
        "architecture.input_modalities",
        "architecture.output_modalities",
        "supported_parameters",
        "context_length",
        "top_provider.max_completion_tokens",
    ),
    api_version="v1",
    priority=90,
)
GOOGLE_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="google.generative-language.models.v1beta",
    provider_id="google",
    endpoint_path="/v1beta/models",
    envelope=ENVELOPE_MODELS,
    identity_paths=("baseModelId", "name"),
    required_item_paths=("supportedGenerationMethods",),
    item_types=(("supportedGenerationMethods", (list, tuple)),),
    capability_paths=(
        "supportedGenerationMethods",
        "inputTokenLimit",
        "outputTokenLimit",
        "thinking",
        "temperature",
        "topP",
        "topK",
    ),
    api_version="v1beta",
    priority=100,
)
OLLAMA_TAGS_SHAPE = ProviderCatalogShape(
    shape_id="ollama.tags.v1",
    provider_id="ollama",
    endpoint_path="/api/tags",
    envelope=ENVELOPE_MODELS,
    identity_paths=("model", "name"),
    required_item_any_paths=("digest", "details.family", "details.families"),
    capability_paths=("details.family", "details.families"),
    priority=90,
)
OLLAMA_SHOW_SHAPE = ProviderCatalogShape(
    shape_id="ollama.show.v1",
    provider_id="ollama",
    endpoint_path="/api/show",
    envelope=ENVELOPE_SINGLE,
    identity_paths=(),
    required_item_paths=("capabilities",),
    required_item_any_paths=("model_info", "details", "template", "parameters"),
    item_types=(("capabilities", (list, tuple)),),
    capability_paths=("capabilities", "model_info.*.context_length"),
    priority=100,
)
LMSTUDIO_MODELS_V1_SHAPE = ProviderCatalogShape(
    shape_id="lmstudio.models.native.v1",
    provider_id="lmstudio",
    endpoint_path="/api/v1/models",
    envelope=ENVELOPE_MODELS,
    identity_paths=("key",),
    required_item_paths=("type",),
    required_item_any_paths=("capabilities", "loaded_instances", "max_context_length", "architecture", "quantization"),
    item_types=(("type", (str,)),),
    capability_paths=("type", "capabilities", "max_context_length", "loaded_instances[].config.context_length"),
    api_version="1",
    priority=100,
)
LMSTUDIO_MODELS_V0_SHAPE = ProviderCatalogShape(
    shape_id="lmstudio.models.native.v0",
    provider_id="lmstudio",
    endpoint_path="/api/v0/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("type",),
    required_item_any_paths=("arch", "compatibility_type", "state", "max_context_length"),
    item_types=(("type", (str,)),),
    capability_paths=("type", "max_context_length"),
    api_version="0",
    priority=80,
    latest=False,
)
LLAMACPP_PROPS_SHAPE = ProviderCatalogShape(
    shape_id="llamacpp.props.v1",
    provider_id="llamacpp",
    endpoint_path="/props",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("model_alias", "model_path"),
    required_item_paths=("default_generation_settings",),
    required_item_any_paths=("chat_template_caps", "modalities", "total_slots"),
    item_types=(("default_generation_settings", (Mapping,)),),
    capability_paths=(
        "chat_template_caps",
        "modalities",
        "default_generation_settings.n_ctx",
        "default_generation_settings.params",
    ),
    priority=100,
)
MISTRAL_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="mistral.models.rich.v1",
    provider_id="mistral",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("capabilities",),
    required_item_any_paths=(
        "capabilities.completion_chat",
        "capabilities.completion_fim",
        "capabilities.function_calling",
        "capabilities.vision",
        "capabilities.classification",
    ),
    item_types=(("capabilities", (Mapping,)),),
    capability_paths=("capabilities", "max_context_length"),
    api_version="v1",
    priority=100,
)
COPILOT_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="github-copilot.models.v1",
    provider_id="copilot",
    endpoint_path="/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("model_picker_enabled", "capabilities.supports"),
    item_types=(
        ("model_picker_enabled", (bool,)),
        ("capabilities.supports", (Mapping,)),
    ),
    capability_paths=("capabilities.supports.tool_calls", "capabilities.supports.vision"),
    priority=100,
)
ANTHROPIC_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="anthropic.models.identity.v1",
    provider_id="anthropic",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("type", "display_name", "created_at"),
    item_values=(("type", ("model",)),),
    api_version="2023-06-01",
    priority=70,
)
CHATGPT_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="chatgpt-subscription.codex-models.v1",
    provider_id="chatgpt_subscription",
    endpoint_path="/backend-api/codex/models?client_version=1.0.0",
    envelope=ENVELOPE_MODELS,
    identity_paths=("slug",),
    required_item_any_paths=("visibility", "priority"),
    priority=90,
)
SGLANG_MODEL_INFO_SHAPE = ProviderCatalogShape(
    shape_id="sglang.model-info.v2",
    provider_id="sglang",
    endpoint_path="/model_info",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("model_path",),
    required_item_paths=("is_generation",),
    required_item_any_paths=("tokenizer_path", "has_image_understanding", "has_audio_understanding"),
    item_types=(("is_generation", (bool,)),),
    capability_paths=("is_generation", "has_image_understanding", "has_audio_understanding", "preferred_sampling_params"),
    api_version="2",
    priority=100,
)
SGLANG_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="sglang.models.openai.v1",
    provider_id="sglang",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("root", "max_model_len"),
    item_values=(("owned_by", ("sglang",)),),
    capability_paths=("max_model_len",),
    priority=80,
)
VLLM_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="vllm.models.openai.v1",
    provider_id="vllm",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("root", "max_model_len", "permission"),
    item_values=(("owned_by", ("vllm",)),),
    capability_paths=("max_model_len",),
    priority=80,
)
HUGGINGFACE_MODEL_SHAPE = ProviderCatalogShape(
    shape_id="huggingface.hub.model-info.v1",
    provider_id="huggingface",
    endpoint_path="/api/models/{model}",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("modelId", "id"),
    required_item_paths=("pipeline_tag",),
    item_types=(("pipeline_tag", (str,)),),
    capability_paths=("pipeline_tag", "tags", "config"),
    priority=80,
)
COHERE_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="cohere.models.rich.v1",
    provider_id="cohere",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_MODELS,
    identity_paths=("name",),
    required_item_paths=("endpoints",),
    required_item_any_paths=("context_length", "default_endpoints", "features", "sampling_defaults"),
    item_types=(("endpoints", (list, tuple)),),
    capability_paths=("endpoints", "context_length", "sampling_defaults"),
    api_version="v1",
    priority=100,
)
MINIMAX_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="minimax.models.identity.v1",
    provider_id="minimax",
    endpoint_path="/v1/models",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("object", "owned_by"),
    item_values=(("object", ("model",)), ("owned_by", ("minimax",))),
    api_version="v1",
    priority=90,
)


def _provider(
    provider_id: str,
    display_name: str,
    *,
    aliases: tuple[str, ...] = (),
    hosts: tuple[str, ...] = (),
    api_shapes: tuple[ProviderApiShape, ...] = (OPENAI_CHAT_SHAPE,),
    catalog_shapes: tuple[ProviderCatalogShape, ...] = (),
) -> ProviderCapabilitySchema:
    return ProviderCapabilitySchema(
        provider_id=provider_id,
        display_name=display_name,
        aliases=aliases,
        host_suffixes=hosts,
        api_shapes=api_shapes,
        catalog_shapes=catalog_shapes,
    )


PROVIDER_SCHEMAS = {
    PROVIDER_GENERIC_OPENAI: _provider(
        PROVIDER_GENERIC_OPENAI,
        "General OpenAI-compatible",
        aliases=("openai_compatible", "proxy"),
        api_shapes=(OPENAI_CHAT_SHAPE, OPENAI_RESPONSES_SHAPE),
        catalog_shapes=(GENERAL_DATA_SHAPE, GENERAL_MODELS_SHAPE, GENERAL_BARE_SHAPE),
    ),
    "openai": _provider(
        "openai",
        "OpenAI",
        hosts=("openai.com",),
        api_shapes=(OPENAI_CHAT_SHAPE, OPENAI_RESPONSES_SHAPE),
        catalog_shapes=(OPENAI_MODELS_SHAPE,),
    ),
    "openrouter": _provider(
        "openrouter",
        "OpenRouter",
        hosts=("openrouter.ai",),
        catalog_shapes=(OPENROUTER_MODELS_SHAPE,),
    ),
    "google": _provider(
        "google",
        "Google Gemini",
        aliases=("gemini", "google_ai_studio"),
        hosts=("generativelanguage.googleapis.com",),
        api_shapes=(GOOGLE_CONTENT_SHAPE, OPENAI_CHAT_SHAPE),
        catalog_shapes=(GOOGLE_MODELS_SHAPE,),
    ),
    "anthropic": _provider(
        "anthropic",
        "Anthropic",
        hosts=("anthropic.com",),
        api_shapes=(ANTHROPIC_MESSAGES_SHAPE,),
        catalog_shapes=(ANTHROPIC_MODELS_SHAPE,),
    ),
    "ollama": _provider(
        "ollama",
        "Ollama",
        hosts=("ollama.com",),
        api_shapes=(OLLAMA_NATIVE_SHAPE, OPENAI_CHAT_SHAPE),
        catalog_shapes=(OLLAMA_SHOW_SHAPE, OLLAMA_TAGS_SHAPE),
    ),
    "lmstudio": _provider(
        "lmstudio",
        "LM Studio",
        aliases=("lm_studio",),
        api_shapes=(LMSTUDIO_NATIVE_SHAPE, OPENAI_CHAT_SHAPE, OPENAI_RESPONSES_SHAPE),
        catalog_shapes=(LMSTUDIO_MODELS_V1_SHAPE, LMSTUDIO_MODELS_V0_SHAPE),
    ),
    "llamacpp": _provider(
        "llamacpp",
        "llama.cpp",
        aliases=("llama.cpp", "llama_cpp", "llama_server"),
        api_shapes=(OPENAI_CHAT_SHAPE, OPENAI_RESPONSES_SHAPE, ANTHROPIC_MESSAGES_SHAPE),
        catalog_shapes=(LLAMACPP_PROPS_SHAPE,),
    ),
    "mistral": _provider(
        "mistral",
        "Mistral",
        hosts=("mistral.ai",),
        catalog_shapes=(MISTRAL_MODELS_SHAPE,),
    ),
    "copilot": _provider(
        "copilot",
        "GitHub Copilot",
        aliases=("github_copilot",),
        hosts=("api.githubcopilot.com",),
        catalog_shapes=(COPILOT_MODELS_SHAPE,),
    ),
    "chatgpt_subscription": _provider(
        "chatgpt_subscription",
        "ChatGPT Subscription",
        aliases=("chatgpt-subscription", "chatgpt", "codex_subscription"),
        hosts=("chatgpt.com",),
        api_shapes=(CHATGPT_SUBSCRIPTION_SHAPE,),
        catalog_shapes=(CHATGPT_MODELS_SHAPE,),
    ),
    "sglang": _provider(
        "sglang",
        "SGLang",
        api_shapes=(OPENAI_CHAT_SHAPE, OPENAI_RESPONSES_SHAPE, SGLANG_NATIVE_SHAPE),
        catalog_shapes=(SGLANG_MODEL_INFO_SHAPE, SGLANG_MODELS_SHAPE),
    ),
    "vllm": _provider(
        "vllm",
        "vLLM",
        api_shapes=(OPENAI_CHAT_SHAPE, OPENAI_RESPONSES_SHAPE),
        catalog_shapes=(VLLM_MODELS_SHAPE,),
    ),
    "huggingface": _provider(
        "huggingface",
        "Hugging Face",
        aliases=("hf", "hugging_face"),
        hosts=("huggingface.co",),
        api_shapes=(OPENAI_CHAT_SHAPE,),
        catalog_shapes=(HUGGINGFACE_MODEL_SHAPE,),
    ),
    "cohere": _provider(
        "cohere",
        "Cohere",
        hosts=("cohere.ai", "cohere.com"),
        api_shapes=(COHERE_V2_SHAPE, OPENAI_CHAT_SHAPE),
        catalog_shapes=(COHERE_MODELS_SHAPE,),
    ),
    "minimax": _provider(
        "minimax",
        "MiniMax",
        hosts=("minimax.io", "minimaxi.com"),
        api_shapes=(ANTHROPIC_MESSAGES_SHAPE, OPENAI_CHAT_SHAPE),
        catalog_shapes=(MINIMAX_MODELS_SHAPE,),
    ),
}

# These providers are currently OpenAI-compatible in Odysseus and have no
# provider-reported capability catalog shape that is stronger than the general
# structural fallback.  Keeping individual identities prevents transport
# quirks from being flattened into "OpenAI" while capability stays per-model.
_GENERAL_PROVIDER_ALIASES = {
    "moonshot": ("moonshot_ai",),
    "nvidia": ("nvidia_nim", "nim"),
    "xai": ("x_ai",),
    "zai": ("z.ai", "z_ai"),
    "opencode": ("opencode_go", "opencode_zen"),
    "together": ("together_ai",),
    "fireworks": ("fireworks_ai",),
    "atlas_cloud": ("atlas",),
    "azure_openai": ("azure",),
    "bedrock": ("aws_bedrock",),
    "cloudflare_workers_ai": ("workers_ai",),
    "mlx_lm": ("mlx",),
    "text_generation_inference": ("tgi", "huggingface_tgi", "hugging_face_tgi"),
}
for _provider_id, _display, _hosts in (
    ("moonshot", "Moonshot AI", ("moonshot.ai", "moonshot.cn")),
    ("groq", "Groq", ("groq.com",)),
    ("nvidia", "NVIDIA NIM", ("nvidia.com",)),
    ("cerebras", "Cerebras", ("cerebras.ai",)),
    ("deepseek", "DeepSeek", ("deepseek.com",)),
    ("together", "Together AI", ("together.xyz", "together.ai")),
    ("fireworks", "Fireworks AI", ("fireworks.ai",)),
    ("xai", "xAI", ("x.ai",)),
    ("zai", "Z.AI", ("z.ai",)),
    ("opencode", "OpenCode", ("opencode.ai",)),
    ("perplexity", "Perplexity", ("perplexity.ai",)),
    ("github_models", "GitHub Models", ("models.inference.ai.azure.com",)),
    ("atlas_cloud", "Atlas Cloud", ("atlascloud.ai",)),
    ("siliconflow", "SiliconFlow", ("siliconflow.cn", "siliconflow.com",)),
    ("kimi_code", "Kimi Code", ("kimi.com",)),
    ("venice", "Venice", ("venice.ai",)),
    ("azure_openai", "Azure OpenAI", ("openai.azure.com",)),
    ("bedrock", "AWS Bedrock", ()),
    ("cloudflare_workers_ai", "Cloudflare Workers AI", ()),
    ("mlx_lm", "MLX LM", ()),
    ("text_generation_inference", "Hugging Face TGI", ()),
    ("lmdeploy", "LMDeploy", ()),
    ("litellm", "LiteLLM", ()),
):
    PROVIDER_SCHEMAS[_provider_id] = _provider(
        _provider_id,
        _display,
        aliases=_GENERAL_PROVIDER_ALIASES.get(_provider_id, ()),
        hosts=_hosts,
    )

UNKNOWN_SCHEMA = ProviderCapabilitySchema(
    provider_id=PROVIDER_UNKNOWN,
    display_name="Unknown provider",
    api_shapes=(),
    catalog_shapes=(),
)

_ALIASES = {
    _token(alias): provider_id
    for provider_id, schema in PROVIDER_SCHEMAS.items()
    for alias in (provider_id, *schema.aliases)
}


def normalize_provider_id(value: Any) -> str:
    token = _token(value)
    return _ALIASES.get(token, token if token in PROVIDER_SCHEMAS else PROVIDER_UNKNOWN)


def schema_for_provider(value: Any) -> ProviderCapabilitySchema:
    return PROVIDER_SCHEMAS.get(normalize_provider_id(value), UNKNOWN_SCHEMA)


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def provider_from_host(base_url: Any) -> str:
    try:
        host = (urlparse(str(base_url or "")).hostname or "").lower().rstrip(".")
    except Exception:
        return PROVIDER_UNKNOWN
    if not host:
        return PROVIDER_UNKNOWN
    if host.startswith("copilot-api.") and host.endswith(".ghe.com"):
        return "copilot"
    matches = [
        schema.provider_id
        for schema in PROVIDER_SCHEMAS.values()
        if any(_host_matches(host, suffix) for suffix in schema.host_suffixes)
    ]
    return matches[0] if len(set(matches)) == 1 else PROVIDER_UNKNOWN


def catalog_shape_for_payload(
    payload: Any,
    *,
    provider_id: Any = None,
    include_general: bool = True,
) -> ProviderCatalogShape | None:
    normalized = normalize_provider_id(provider_id)
    provider_is_explicit = normalized not in {PROVIDER_UNKNOWN, PROVIDER_GENERIC_OPENAI}
    if provider_is_explicit:
        schemas = (PROVIDER_SCHEMAS[normalized],)
    else:
        schemas = tuple(PROVIDER_SCHEMAS.values())

    candidates = [
        shape
        for schema in schemas
        for shape in schema.catalog_shapes
        if (provider_is_explicit or shape.priority > 0) and shape.matches(payload)
    ]
    if candidates:
        best_priority = max(shape.priority for shape in candidates)
        best = [shape for shape in candidates if shape.priority == best_priority]
        providers = {shape.provider_id for shape in best}
        if len(providers) == 1:
            return sorted(best, key=lambda shape: shape.shape_id)[0]

    if not include_general:
        return None
    for shape in PROVIDER_SCHEMAS[PROVIDER_GENERIC_OPENAI].catalog_shapes:
        if shape.matches(payload):
            return shape
    return None


def resolve_provider(
    payload: Any = None,
    *,
    provider: Any = None,
    endpoint_kind: Any = None,
    base_url: Any = None,
) -> ProviderResolution:
    explicit = normalize_provider_id(provider)
    if explicit != PROVIDER_UNKNOWN:
        shape = catalog_shape_for_payload(payload, provider_id=explicit) if payload is not None else None
        return ProviderResolution(explicit, RESOLUTION_EXPLICIT, PROVIDER_SCHEMAS[explicit], shape)

    kind = normalize_provider_id(endpoint_kind)
    if kind != PROVIDER_UNKNOWN:
        shape = catalog_shape_for_payload(payload, provider_id=kind) if payload is not None else None
        return ProviderResolution(kind, RESOLUTION_ENDPOINT_KIND, PROVIDER_SCHEMAS[kind], shape)

    host_provider = provider_from_host(base_url)
    if host_provider != PROVIDER_UNKNOWN:
        shape = catalog_shape_for_payload(payload, provider_id=host_provider) if payload is not None else None
        return ProviderResolution(
            host_provider,
            RESOLUTION_HOST,
            PROVIDER_SCHEMAS[host_provider],
            shape,
        )

    shape = catalog_shape_for_payload(payload, include_general=False) if payload is not None else None
    if shape:
        return ProviderResolution(
            shape.provider_id,
            RESOLUTION_NATIVE_SHAPE,
            PROVIDER_SCHEMAS[shape.provider_id],
            shape,
        )

    shape = catalog_shape_for_payload(payload) if payload is not None else None
    if shape:
        return ProviderResolution(
            PROVIDER_GENERIC_OPENAI,
            RESOLUTION_GENERAL_SHAPE,
            PROVIDER_SCHEMAS[PROVIDER_GENERIC_OPENAI],
            shape,
        )

    return ProviderResolution(PROVIDER_UNKNOWN, RESOLUTION_UNKNOWN, UNKNOWN_SCHEMA, None)


__all__ = [
    "ANTHROPIC_MESSAGES_SHAPE",
    "CHATGPT_SUBSCRIPTION_SHAPE",
    "COHERE_V2_SHAPE",
    "DIALECT_ANTHROPIC_MESSAGES",
    "DIALECT_CHATGPT_SUBSCRIPTION",
    "DIALECT_COHERE_V2",
    "DIALECT_GOOGLE_GENERATE_CONTENT",
    "DIALECT_HUGGINGFACE_HUB",
    "DIALECT_LLAMACPP_NATIVE",
    "DIALECT_LMSTUDIO_NATIVE_V1",
    "DIALECT_OLLAMA_NATIVE",
    "DIALECT_OPENAI_CHAT",
    "DIALECT_OPENAI_RESPONSES",
    "DIALECT_SGLANG_NATIVE",
    "GOOGLE_CONTENT_SHAPE",
    "LMSTUDIO_NATIVE_SHAPE",
    "OLLAMA_NATIVE_SHAPE",
    "OPENAI_CHAT_SHAPE",
    "OPENAI_RESPONSES_SHAPE",
    "PROVIDER_GENERIC_OPENAI",
    "PROVIDER_SCHEMAS",
    "PROVIDER_UNKNOWN",
    "ProviderApiShape",
    "ProviderCapabilitySchema",
    "ProviderCatalogShape",
    "ProviderResolution",
    "catalog_shape_for_payload",
    "normalize_provider_id",
    "provider_from_host",
    "resolve_provider",
    "schema_for_provider",
]
