"""Provider identity and native model-catalog shape detection.

The registry has one narrow job: identify a configured provider and recognize
tested provider-native catalog envelopes.  Generic ``data``/``models``/list
envelopes are marked as fallback inventory only; they never promote model
capabilities.

Request/response transport fields and model-specific behavior belong to their
runtime adapters, not this catalog detector.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


PROVIDER_UNKNOWN = "unknown"
PROVIDER_GENERIC_OPENAI = "generic_openai"

PROVIDER_SOURCE_EXPLICIT = "explicit"
PROVIDER_SOURCE_ENDPOINT_KIND = "endpoint_kind"
PROVIDER_SOURCE_HOST = "host"
PROVIDER_SOURCE_PAYLOAD = "payload"
PROVIDER_SOURCE_UNKNOWN = "unknown"

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
    """A tested provider-native shape or an explicit inventory fallback."""

    shape_id: str
    provider_id: str
    envelope: str
    identity_paths: tuple[str, ...]
    required_root_paths: tuple[str, ...] = ()
    required_item_paths: tuple[str, ...] = ()
    required_item_any_paths: tuple[str, ...] = ()
    item_types: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    item_values: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    detection_priority: int = 0
    fallback: bool = False

    def items(self, payload: Any) -> tuple[Mapping[str, Any], ...]:
        return _items_for_envelope(payload, self.envelope)

    def item_matches(self, item: Mapping[str, Any]) -> bool:
        if self.identity_paths and not any(
            (value := _path_value(item, path)) is not _MISSING
            and isinstance(value, str)
            and bool(value.strip())
            for path in self.identity_paths
        ):
            return False
        if not all(_path_present(item, path) for path in self.required_item_paths):
            return False
        if self.required_item_any_paths and not any(
            _path_present(item, path) for path in self.required_item_any_paths
        ):
            return False
        if any(
            not isinstance(_path_value(item, path), expected_types)
            for path, expected_types in self.item_types
        ):
            return False
        if any(_path_value(item, path) not in expected for path, expected in self.item_values):
            return False
        return True

    def matches(self, payload: Any) -> bool:
        if self.required_root_paths:
            if not isinstance(payload, Mapping):
                return False
            if not all(_path_present(payload, path) for path in self.required_root_paths):
                return False
        return any(self.item_matches(item) for item in self.items(payload))

    def payload_for_item(self, payload: Any, item: Mapping[str, Any]) -> Any:
        """Return a one-item payload in the same provider-native envelope."""

        if self.envelope == ENVELOPE_BARE_LIST:
            return [item]
        if self.envelope == ENVELOPE_SINGLE:
            return {
                key: value
                for key, value in item.items()
                if key not in {ENVELOPE_DATA, ENVELOPE_MODELS}
            }
        if isinstance(payload, Mapping):
            narrowed = {
                key: value
                for key, value in payload.items()
                if key not in {ENVELOPE_DATA, ENVELOPE_MODELS}
            }
            narrowed[self.envelope] = [item]
            return narrowed
        return {self.envelope: [item]}


@dataclass(frozen=True)
class ProviderCapabilitySchema:
    provider_id: str
    aliases: tuple[str, ...] = ()
    host_suffixes: tuple[str, ...] = ()
    catalog_shapes: tuple[ProviderCatalogShape, ...] = ()


@dataclass(frozen=True)
class ProviderResolution:
    provider_id: str = PROVIDER_UNKNOWN
    provider_source: str = PROVIDER_SOURCE_UNKNOWN
    shape_id: str = ""
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "provider_source": self.provider_source,
            "shape": self.shape_id,
            "fallback": self.fallback,
        }


# Generic envelopes are inventory fallbacks only.  Their field names are not a
# portable capability contract, so readers may recover identity but nothing
# else from them.
GENERAL_DATA_SHAPE = ProviderCatalogShape(
    shape_id="fallback.models.data.v1",
    provider_id=PROVIDER_UNKNOWN,
    envelope=ENVELOPE_DATA,
    identity_paths=("id", "name", "model", "key", "slug"),
    fallback=True,
)
GENERAL_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="fallback.models.envelope.v1",
    provider_id=PROVIDER_UNKNOWN,
    envelope=ENVELOPE_MODELS,
    identity_paths=("id", "name", "model", "key", "slug"),
    fallback=True,
)
GENERAL_BARE_SHAPE = ProviderCatalogShape(
    shape_id="fallback.models.list.v1",
    provider_id=PROVIDER_UNKNOWN,
    envelope=ENVELOPE_BARE_LIST,
    identity_paths=("id", "name", "model", "key", "slug"),
    fallback=True,
)
FALLBACK_CATALOG_SHAPES = (
    GENERAL_DATA_SHAPE,
    GENERAL_MODELS_SHAPE,
    GENERAL_BARE_SHAPE,
)


OPENAI_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="openai.models.identity.v1",
    provider_id="openai",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("object", "created", "owned_by"),
    item_values=(("object", ("model",)),),
)
OPENROUTER_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="openrouter.models.rich.v1",
    provider_id="openrouter",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=(
        "architecture",
        "canonical_slug",
        "pricing",
        "supported_parameters",
        "top_provider",
    ),
    item_types=(
        ("architecture", (Mapping,)),
        ("canonical_slug", (str,)),
        ("pricing", (Mapping,)),
        ("supported_parameters", (list, tuple)),
        ("top_provider", (Mapping,)),
    ),
    detection_priority=90,
)
GOOGLE_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="google.generative-language.models.v1beta",
    provider_id="google",
    envelope=ENVELOPE_MODELS,
    identity_paths=("baseModelId", "name"),
    required_item_paths=("supportedGenerationMethods",),
    item_types=(("supportedGenerationMethods", (list, tuple)),),
    detection_priority=100,
)
GOOGLE_MODEL_SHAPE = ProviderCatalogShape(
    shape_id="google.generative-language.model.v1beta",
    provider_id="google",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("baseModelId", "name"),
    required_item_paths=("supportedGenerationMethods",),
    item_types=(("supportedGenerationMethods", (list, tuple)),),
    detection_priority=100,
)
OLLAMA_TAGS_SHAPE = ProviderCatalogShape(
    shape_id="ollama.tags.v1",
    provider_id="ollama",
    envelope=ENVELOPE_MODELS,
    identity_paths=("model", "name"),
    required_item_any_paths=("digest", "details.family", "details.families"),
    # `name` plus a digest/details field is not globally provider-specific.
    # Configured provider context remains authoritative for local Ollama
    # inventories; payload-only detection would create false provider identity.
    detection_priority=0,
)
OLLAMA_SHOW_SHAPE = ProviderCatalogShape(
    shape_id="ollama.show.v1",
    provider_id="ollama",
    envelope=ENVELOPE_SINGLE,
    identity_paths=(),
    required_item_paths=("capabilities",),
    required_item_any_paths=("model_info", "details", "template", "parameters"),
    item_types=(("capabilities", (list, tuple)),),
    # `/api/show` capability and parameter fields are not sufficiently unique
    # to identify an otherwise unknown provider.  Local/default ports are also
    # deliberately non-authoritative, so require configured provider context
    # before interpreting this singleton response as Ollama-native metadata.
    detection_priority=0,
)
LMSTUDIO_MODELS_V1_SHAPE = ProviderCatalogShape(
    shape_id="lmstudio.models.native.v1",
    provider_id="lmstudio",
    envelope=ENVELOPE_MODELS,
    identity_paths=("key",),
    required_item_paths=("type",),
    required_item_any_paths=(
        "capabilities",
        "loaded_instances",
        "max_context_length",
        "architecture",
        "quantization",
    ),
    item_types=(("type", (str,)),),
    detection_priority=0,
)
LMSTUDIO_MODELS_V0_SHAPE = ProviderCatalogShape(
    shape_id="lmstudio.models.native.v0",
    provider_id="lmstudio",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("type",),
    required_item_any_paths=("arch", "compatibility_type", "state", "max_context_length"),
    item_types=(("type", (str,)),),
    detection_priority=0,
)
LLAMACPP_PROPS_SHAPE = ProviderCatalogShape(
    shape_id="llamacpp.props.v1",
    provider_id="llamacpp",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("model_alias", "model_path"),
    required_item_paths=("default_generation_settings",),
    required_item_any_paths=("chat_template_caps", "modalities", "total_slots"),
    item_types=(("default_generation_settings", (Mapping,)),),
    detection_priority=100,
)
LLAMACPP_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="llamacpp.models.native.v1",
    provider_id="llamacpp",
    envelope=ENVELOPE_MODELS,
    identity_paths=("id", "name", "model"),
    required_item_paths=("capabilities",),
    item_types=(("capabilities", (list, tuple)),),
    # Model/capability fields are not globally provider-specific. Interpret
    # them only after explicit llama.cpp endpoint/provider selection.
    detection_priority=0,
)
MISTRAL_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="mistral.models.rich.v1",
    provider_id="mistral",
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
    detection_priority=0,
)
COPILOT_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="github-copilot.models.v1",
    provider_id="copilot",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("model_picker_enabled", "capabilities.supports"),
    item_types=(
        ("model_picker_enabled", (bool,)),
        ("capabilities.supports", (Mapping,)),
    ),
    detection_priority=100,
)
ANTHROPIC_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="anthropic.models.identity.v1",
    provider_id="anthropic",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("type", "display_name", "created_at"),
    item_values=(("type", ("model",)),),
    # These model-resource fields are not globally provider-specific. Require
    # explicit Anthropic endpoint/provider context before assigning identity.
    detection_priority=0,
)
CHATGPT_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="chatgpt-subscription.codex-models.v1",
    provider_id="chatgpt_subscription",
    envelope=ENVELOPE_MODELS,
    identity_paths=("slug",),
    required_item_any_paths=("visibility", "priority"),
    detection_priority=0,
)
SGLANG_MODEL_INFO_SHAPE = ProviderCatalogShape(
    shape_id="sglang.model-info.v2",
    provider_id="sglang",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("model_path",),
    required_item_paths=("is_generation",),
    required_item_any_paths=(
        "tokenizer_path",
        "has_image_understanding",
        "has_audio_understanding",
    ),
    item_types=(("is_generation", (bool,)),),
    detection_priority=100,
)
SGLANG_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="sglang.models.openai.v1",
    provider_id="sglang",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("root", "max_model_len"),
    item_values=(("owned_by", ("sglang",)),),
    detection_priority=80,
)
VLLM_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="vllm.models.openai.v1",
    provider_id="vllm",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("root", "max_model_len", "permission"),
    item_values=(("owned_by", ("vllm",)),),
    detection_priority=80,
)
HUGGINGFACE_MODEL_SHAPE = ProviderCatalogShape(
    shape_id="huggingface.hub.model-info.v1",
    provider_id="huggingface",
    envelope=ENVELOPE_SINGLE,
    identity_paths=("modelId", "id"),
    # Hub ModelInfo exposes pipeline_tag as optional metadata.  Provider/host
    # context is still required because this shape has priority zero, so an
    # identity-only card can stay native without making generic ``id`` payloads
    # look like Hugging Face catalogs.
    detection_priority=0,
)
HUGGINGFACE_MODELS_LIST_SHAPE = ProviderCatalogShape(
    shape_id="huggingface.hub.model-info-list.v1",
    provider_id="huggingface",
    envelope=ENVELOPE_BARE_LIST,
    identity_paths=("modelId", "id"),
    detection_priority=0,
)
COHERE_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="cohere.models.rich.v1",
    provider_id="cohere",
    envelope=ENVELOPE_MODELS,
    identity_paths=("name",),
    required_item_paths=("endpoints",),
    required_item_any_paths=(
        "context_length",
        "default_endpoints",
        "features",
        "sampling_defaults",
    ),
    item_types=(("endpoints", (list, tuple)),),
    detection_priority=0,
)
MINIMAX_MODELS_SHAPE = ProviderCatalogShape(
    shape_id="minimax.models.identity.v1",
    provider_id="minimax",
    envelope=ENVELOPE_DATA,
    identity_paths=("id",),
    required_item_paths=("object", "owned_by"),
    item_values=(("object", ("model",)), ("owned_by", ("minimax",))),
    detection_priority=90,
)


def _provider(
    provider_id: str,
    *,
    aliases: tuple[str, ...] = (),
    hosts: tuple[str, ...] = (),
    shapes: tuple[ProviderCatalogShape, ...] = (),
) -> ProviderCapabilitySchema:
    return ProviderCapabilitySchema(
        provider_id=provider_id,
        aliases=aliases,
        host_suffixes=hosts,
        catalog_shapes=shapes,
    )


PROVIDER_SCHEMAS = {
    PROVIDER_GENERIC_OPENAI: _provider(
        PROVIDER_GENERIC_OPENAI,
        aliases=("openai_compatible", "openai_compat"),
    ),
    "openai": _provider("openai", hosts=("openai.com",), shapes=(OPENAI_MODELS_SHAPE,)),
    "openrouter": _provider(
        "openrouter",
        hosts=("openrouter.ai",),
        shapes=(OPENROUTER_MODELS_SHAPE,),
    ),
    "google": _provider(
        "google",
        aliases=("gemini", "google_ai_studio"),
        hosts=("generativelanguage.googleapis.com",),
        shapes=(GOOGLE_MODELS_SHAPE, GOOGLE_MODEL_SHAPE),
    ),
    "anthropic": _provider(
        "anthropic",
        hosts=("anthropic.com",),
        shapes=(ANTHROPIC_MODELS_SHAPE,),
    ),
    "ollama": _provider(
        "ollama",
        hosts=("ollama.com",),
        shapes=(OLLAMA_SHOW_SHAPE, OLLAMA_TAGS_SHAPE),
    ),
    "lmstudio": _provider(
        "lmstudio",
        aliases=("lm_studio",),
        shapes=(LMSTUDIO_MODELS_V1_SHAPE, LMSTUDIO_MODELS_V0_SHAPE),
    ),
    "llamacpp": _provider(
        "llamacpp",
        aliases=("llama.cpp", "llama_cpp", "llama_server"),
        shapes=(LLAMACPP_PROPS_SHAPE, LLAMACPP_MODELS_SHAPE),
    ),
    "mistral": _provider(
        "mistral",
        hosts=("mistral.ai",),
        shapes=(MISTRAL_MODELS_SHAPE,),
    ),
    "copilot": _provider(
        "copilot",
        aliases=("github_copilot",),
        hosts=("api.githubcopilot.com",),
        shapes=(COPILOT_MODELS_SHAPE,),
    ),
    "chatgpt_subscription": _provider(
        "chatgpt_subscription",
        aliases=("chatgpt-subscription", "chatgpt", "codex_subscription"),
        hosts=("chatgpt.com",),
        shapes=(CHATGPT_MODELS_SHAPE,),
    ),
    "sglang": _provider(
        "sglang",
        shapes=(SGLANG_MODEL_INFO_SHAPE, SGLANG_MODELS_SHAPE),
    ),
    "vllm": _provider("vllm", shapes=(VLLM_MODELS_SHAPE,)),
    "huggingface": _provider(
        "huggingface",
        aliases=("hf", "hugging_face"),
        hosts=("huggingface.co",),
        shapes=(HUGGINGFACE_MODEL_SHAPE, HUGGINGFACE_MODELS_LIST_SHAPE),
    ),
    "cohere": _provider(
        "cohere",
        hosts=("cohere.ai", "cohere.com"),
        shapes=(COHERE_MODELS_SHAPE,),
    ),
    "minimax": _provider(
        "minimax",
        hosts=("minimax.io", "minimaxi.com"),
        shapes=(MINIMAX_MODELS_SHAPE,),
    ),
}

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
for _provider_id, _hosts in (
    ("moonshot", ("moonshot.ai", "moonshot.cn")),
    ("groq", ("groq.com",)),
    ("nvidia", ("nvidia.com",)),
    ("cerebras", ("cerebras.ai",)),
    ("deepseek", ("deepseek.com",)),
    ("together", ("together.xyz", "together.ai")),
    ("fireworks", ("fireworks.ai",)),
    ("xai", ("x.ai",)),
    ("zai", ("z.ai",)),
    ("opencode", ("opencode.ai",)),
    ("perplexity", ("perplexity.ai",)),
    ("github_models", ("models.inference.ai.azure.com",)),
    ("atlas_cloud", ("atlascloud.ai",)),
    ("siliconflow", ("siliconflow.cn", "siliconflow.com")),
    ("kimi_code", ("kimi.com",)),
    ("venice", ("venice.ai",)),
    ("azure_openai", ("openai.azure.com",)),
    ("bedrock", ()),
    ("cloudflare_workers_ai", ()),
    ("mlx_lm", ()),
    ("text_generation_inference", ()),
    ("lmdeploy", ()),
    ("litellm", ()),
):
    PROVIDER_SCHEMAS[_provider_id] = _provider(
        _provider_id,
        aliases=_GENERAL_PROVIDER_ALIASES.get(_provider_id, ()),
        hosts=_hosts,
    )

UNKNOWN_SCHEMA = ProviderCapabilitySchema(provider_id=PROVIDER_UNKNOWN)

_ALIASES = {
    _token(alias): provider_id
    for provider_id, schema in PROVIDER_SCHEMAS.items()
    for alias in (provider_id, *schema.aliases)
}


def normalize_provider_id(value: Any) -> str:
    token = _token(value)
    if not token or token == PROVIDER_UNKNOWN:
        return PROVIDER_UNKNOWN
    # An explicit, previously unseen provider id is still useful identity.  It
    # selects the inventory-only reader until a native schema is added; it does
    # not acquire capabilities merely by being preserved here.
    return _ALIASES.get(token, token)


def schema_for_provider(value: Any) -> ProviderCapabilitySchema:
    return PROVIDER_SCHEMAS.get(normalize_provider_id(value), UNKNOWN_SCHEMA)


def provider_from_endpoint_kind(value: Any) -> str:
    """Return a provider only for registered provider-valued endpoint kinds.

    Endpoint configuration normally stores transport categories such as
    ``auto``, ``local``, ``api``, and ``proxy``.  Those categories and unknown
    values must not preempt provider identity from a host or native payload.
    """

    provider_id = normalize_provider_id(value)
    return provider_id if provider_id in PROVIDER_SCHEMAS else PROVIDER_UNKNOWN


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
    matches = {
        schema.provider_id
        for schema in PROVIDER_SCHEMAS.values()
        if any(_host_matches(host, suffix) for suffix in schema.host_suffixes)
    }
    return next(iter(matches)) if len(matches) == 1 else PROVIDER_UNKNOWN


def native_shape_for_payload(
    payload: Any,
    *,
    provider_id: Any = None,
) -> ProviderCatalogShape | None:
    normalized = normalize_provider_id(provider_id)
    if normalized == PROVIDER_UNKNOWN:
        shapes = tuple(
            shape
            for schema in PROVIDER_SCHEMAS.values()
            for shape in schema.catalog_shapes
            if shape.detection_priority > 0
        )
    else:
        schema = PROVIDER_SCHEMAS.get(normalized)
        shapes = schema.catalog_shapes if schema else ()

    matches = [shape for shape in shapes if shape.matches(payload)]
    if not matches:
        return None
    providers = {shape.provider_id for shape in matches}
    if len(providers) != 1:
        return None
    priority = max(shape.detection_priority for shape in matches)
    best = [shape for shape in matches if shape.detection_priority == priority]
    # Registry declaration order expresses preference between revisions of the
    # same provider shape (for example LM Studio v1 before v0).  Alphabetical
    # shape ids invert that version preference for otherwise equal evidence.
    return best[0]


def catalog_shape_for_id(shape_id: Any) -> ProviderCatalogShape | None:
    return next(
        (
            shape
            for shape in (
                *FALLBACK_CATALOG_SHAPES,
                *(
                    provider_shape
                    for schema in PROVIDER_SCHEMAS.values()
                    for provider_shape in schema.catalog_shapes
                ),
            )
            if shape.shape_id == shape_id
        ),
        None,
    )


def fallback_shape_for_payload(payload: Any) -> ProviderCatalogShape | None:
    return next((shape for shape in FALLBACK_CATALOG_SHAPES if shape.matches(payload)), None)


def resolve_provider(
    payload: Any = None,
    *,
    provider: Any = None,
    endpoint_kind: Any = None,
    base_url: Any = None,
) -> ProviderResolution:
    provider_id = normalize_provider_id(provider)
    provider_source = PROVIDER_SOURCE_EXPLICIT

    if provider_id == PROVIDER_UNKNOWN:
        provider_id = provider_from_endpoint_kind(endpoint_kind)
        provider_source = PROVIDER_SOURCE_ENDPOINT_KIND
    if provider_id == PROVIDER_UNKNOWN:
        provider_id = provider_from_host(base_url)
        provider_source = PROVIDER_SOURCE_HOST

    if provider_id != PROVIDER_UNKNOWN:
        native = (
            native_shape_for_payload(payload, provider_id=provider_id)
            if payload is not None
            else None
        )
        if native:
            return ProviderResolution(provider_id, provider_source, native.shape_id, False)
        fallback = fallback_shape_for_payload(payload) if payload is not None else None
        return ProviderResolution(
            provider_id,
            provider_source,
            fallback.shape_id if fallback else "",
            bool(fallback),
        )

    native = native_shape_for_payload(payload) if payload is not None else None
    if native:
        return ProviderResolution(
            native.provider_id,
            PROVIDER_SOURCE_PAYLOAD,
            native.shape_id,
            False,
        )

    fallback = fallback_shape_for_payload(payload) if payload is not None else None
    return ProviderResolution(
        PROVIDER_UNKNOWN,
        PROVIDER_SOURCE_UNKNOWN,
        fallback.shape_id if fallback else "",
        bool(fallback),
    )


__all__ = [
    "FALLBACK_CATALOG_SHAPES",
    "GENERAL_BARE_SHAPE",
    "GENERAL_DATA_SHAPE",
    "GENERAL_MODELS_SHAPE",
    "PROVIDER_GENERIC_OPENAI",
    "PROVIDER_SCHEMAS",
    "PROVIDER_SOURCE_ENDPOINT_KIND",
    "PROVIDER_SOURCE_EXPLICIT",
    "PROVIDER_SOURCE_HOST",
    "PROVIDER_SOURCE_PAYLOAD",
    "PROVIDER_SOURCE_UNKNOWN",
    "PROVIDER_UNKNOWN",
    "ProviderCapabilitySchema",
    "ProviderCatalogShape",
    "ProviderResolution",
    "catalog_shape_for_id",
    "fallback_shape_for_payload",
    "native_shape_for_payload",
    "normalize_provider_id",
    "provider_from_endpoint_kind",
    "provider_from_host",
    "resolve_provider",
    "schema_for_provider",
]
