"""Canonical model capability metadata helpers.

This module defines shape and normalization only. It does not probe providers,
change routing, or infer authoritative capabilities from a bare model ID.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


FAMILY_CHAT = "chat"
FAMILY_EMBEDDING = "embedding"
FAMILY_IMAGE = "image"
FAMILY_VIDEO = "video"
FAMILY_AUDIO = "audio"
FAMILY_RERANK = "rerank"
FAMILY_CLASSIFICATION = "classification"
FAMILY_MODERATION = "moderation"
FAMILY_UNKNOWN = "unknown"

FAMILIES = frozenset(
    {
        FAMILY_CHAT,
        FAMILY_EMBEDDING,
        FAMILY_IMAGE,
        FAMILY_VIDEO,
        FAMILY_AUDIO,
        FAMILY_RERANK,
        FAMILY_CLASSIFICATION,
        FAMILY_MODERATION,
        FAMILY_UNKNOWN,
    }
)

MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_FILE = "file"
MODALITY_PDF = "pdf"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"
MODALITY_EMBEDDING = "embedding"

MODALITIES = frozenset(
    {
        MODALITY_TEXT,
        MODALITY_IMAGE,
        MODALITY_FILE,
        MODALITY_PDF,
        MODALITY_AUDIO,
        MODALITY_VIDEO,
        MODALITY_EMBEDDING,
    }
)

CAP_VISION = "vision"
CAP_FILES = "files"
CAP_PDF = "pdf"
CAP_AUDIO_INPUT = "audio_input"
CAP_AUDIO_OUTPUT = "audio_output"
CAP_IMAGE_GENERATION = "image_generation"
CAP_IMAGE_EDITING = "image_editing"
CAP_INPAINTING = "inpainting"
CAP_VIDEO_GENERATION = "video_generation"
CAP_REASONING = "reasoning"
CAP_TOOL_CALL = "tool_call"
CAP_STRUCTURED_OUTPUT = "structured_output"
CAP_WEB_SEARCH = "web_search"
CAP_STREAMING = "streaming"
CAP_JSON_MODE = "json_mode"
CAP_TRANSCRIPTION = "transcription"
CAP_TTS = "tts"
CAP_REALTIME = "realtime"
CAP_TEXT_RENDERING = "text_rendering"

CAPABILITIES = frozenset(
    {
        CAP_VISION,
        CAP_FILES,
        CAP_PDF,
        CAP_AUDIO_INPUT,
        CAP_AUDIO_OUTPUT,
        CAP_IMAGE_GENERATION,
        CAP_IMAGE_EDITING,
        CAP_INPAINTING,
        CAP_VIDEO_GENERATION,
        CAP_REASONING,
        CAP_TOOL_CALL,
        CAP_STRUCTURED_OUTPUT,
        CAP_WEB_SEARCH,
        CAP_STREAMING,
        CAP_JSON_MODE,
        CAP_TRANSCRIPTION,
        CAP_TTS,
        CAP_REALTIME,
        CAP_TEXT_RENDERING,
    }
)

SOURCE_ADMIN_OVERRIDE = "admin_override"
SOURCE_ENDPOINT_CONFIG = "endpoint_config"
SOURCE_PROVIDER_READER = "provider_reader"
SOURCE_COOKBOOK_HF = "cookbook_hf"
SOURCE_MODELS_DEV_REGISTRY = "models_dev_registry"
SOURCE_PROVIDER_DOCS_REGISTRY = "provider_docs_registry"
SOURCE_HEURISTIC = "heuristic"
SOURCE_CAPABILITY_PROBE = "capability_probe"
SOURCE_UNKNOWN = "unknown"

SOURCES = frozenset(
    {
        SOURCE_ADMIN_OVERRIDE,
        SOURCE_ENDPOINT_CONFIG,
        SOURCE_PROVIDER_READER,
        SOURCE_COOKBOOK_HF,
        SOURCE_MODELS_DEV_REGISTRY,
        SOURCE_PROVIDER_DOCS_REGISTRY,
        SOURCE_HEURISTIC,
        SOURCE_CAPABILITY_PROBE,
        SOURCE_UNKNOWN,
    }
)

CONFIDENCE_EXPLICIT = "explicit"
CONFIDENCE_PROVIDER_REPORTED = "provider_reported"
CONFIDENCE_REGISTRY = "registry"
CONFIDENCE_HEURISTIC = "heuristic"
CONFIDENCE_UNKNOWN = "unknown"

CONFIDENCES = frozenset(
    {
        CONFIDENCE_EXPLICIT,
        CONFIDENCE_PROVIDER_REPORTED,
        CONFIDENCE_REGISTRY,
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_UNKNOWN,
    }
)

ASSERTION_CLAIMED = "claimed"
ASSERTION_VERIFIED = "verified"
ASSERTION_UNSUPPORTED = "unsupported"
ASSERTION_UNKNOWN = "unknown"

ASSERTION_STATUSES = frozenset(
    {
        ASSERTION_CLAIMED,
        ASSERTION_VERIFIED,
        ASSERTION_UNSUPPORTED,
        ASSERTION_UNKNOWN,
    }
)

PROBE_PASS = "pass"
PROBE_FAIL = "fail"
PROBE_PARTIAL = "partial"

PROBE_STATUSES = frozenset(
    {
        PROBE_PASS,
        PROBE_FAIL,
        PROBE_PARTIAL,
    }
)

CONTROL_TEMPERATURE = "temperature"
CONTROL_TOP_P = "top_p"
CONTROL_TOP_K = "top_k"
CONTROL_SEED = "seed"
CONTROL_MODEL_VERSION_PIN = "model_version_pin"
CONTROL_STRICT_SCHEMA = "strict_schema"
CONTROL_TOOL_CHOICE = "tool_choice"
CONTROL_SYSTEM_PROMPT = "system_prompt"
CONTROL_PROMPT_CACHING = "prompt_caching"
CONTROL_BATCH = "batch"
CONTROL_REQUEST_HASH_CACHE = "request_hash_cache"
CONTROL_SYSTEM_FINGERPRINT = "system_fingerprint"

# Canonical reasoning control mechanisms describe how a serving path accepts
# reasoning controls. They are provider/engine evidence, not user preferences.
REASONING_CONTROL_MESSAGE_DIRECTIVE = "reasoning_message_directive"  # User-message soft switch, e.g. /think or /no_think.
REASONING_CONTROL_SYSTEM_DIRECTIVE = "reasoning_system_directive"  # System prompt instruction, e.g. "detailed thinking on/off".
REASONING_CONTROL_TEMPLATE_KWARG = "reasoning_template_kwarg"  # Chat-template kwarg, e.g. chat_template_kwargs.enable_thinking.
REASONING_CONTROL_NATIVE_BOOL = "reasoning_native_bool"  # Direct API boolean, e.g. think: true/false.
REASONING_CONTROL_STRUCTURED_OBJECT = "reasoning_structured_object"  # Structured API object, e.g. thinking: {type: "..."}.
REASONING_CONTROL_BUDGET = "reasoning_budget"  # Token budget control, e.g. thinkingBudget: 0/-1/N.
REASONING_CONTROL_EFFORT = "reasoning_effort"  # Graded effort control, e.g. low/medium/high.

# Canonical reasoning control values describe what the provider control accepts.
# Odysseus runtime preferences can also use auto/on/off, but that is a separate
# layer that later code resolves into these provider-specific controls.
REASONING_CONTROL_VALUE_ON = "on"  # Provider supports explicitly requesting reasoning on.
REASONING_CONTROL_VALUE_OFF = "off"  # Provider supports explicitly requesting reasoning off.
REASONING_CONTROL_VALUE_AUTO = "auto"  # Provider supports adaptive/dynamic/vendor-decided reasoning.

REASONING_CONTROL_MECHANISMS = frozenset(
    {
        REASONING_CONTROL_MESSAGE_DIRECTIVE,
        REASONING_CONTROL_SYSTEM_DIRECTIVE,
        REASONING_CONTROL_TEMPLATE_KWARG,
        REASONING_CONTROL_NATIVE_BOOL,
        REASONING_CONTROL_STRUCTURED_OBJECT,
        REASONING_CONTROL_BUDGET,
        REASONING_CONTROL_EFFORT,
    }
)

REASONING_CONTROL_VALUES = frozenset(
    {
        REASONING_CONTROL_VALUE_ON,
        REASONING_CONTROL_VALUE_OFF,
        REASONING_CONTROL_VALUE_AUTO,
    }
)

DETERMINISTIC_CONTROLS = frozenset(
    {
        CONTROL_TEMPERATURE,
        CONTROL_TOP_P,
        CONTROL_TOP_K,
        CONTROL_SEED,
        CONTROL_MODEL_VERSION_PIN,
        CONTROL_STRICT_SCHEMA,
        CONTROL_TOOL_CHOICE,
        CONTROL_SYSTEM_PROMPT,
        CONTROL_PROMPT_CACHING,
        CONTROL_BATCH,
        CONTROL_REQUEST_HASH_CACHE,
        CONTROL_SYSTEM_FINGERPRINT,
    }
)

TASK_CHAT_COMPLETIONS = "chat.completions"
TASK_EMBEDDINGS_CREATE = "embeddings.create"
TASK_IMAGE_GENERATE = "image.generate"
TASK_IMAGE_EDIT = "image.edit"
TASK_VIDEO_GENERATE = "video.generate"
TASK_AUDIO_TRANSCRIBE = "audio.transcribe"
TASK_AUDIO_SYNTHESIZE = "audio.synthesize"
TASK_RERANK = "rerank.score"
TASK_CLASSIFY = "classification.classify"
TASK_MODERATE = "moderation.moderate"
TASK_UNKNOWN = "unknown"

_FAMILY_ALIASES = {
    "llm": FAMILY_CHAT,
    "text": FAMILY_CHAT,
    "text2text": FAMILY_CHAT,
    "chat_completion": FAMILY_CHAT,
    "chat_completions": FAMILY_CHAT,
    "embeddings": FAMILY_EMBEDDING,
    "embed": FAMILY_EMBEDDING,
    "image_generation": FAMILY_IMAGE,
    "image_editing": FAMILY_IMAGE,
    "video_generation": FAMILY_VIDEO,
    "speech": FAMILY_AUDIO,
    "stt": FAMILY_AUDIO,
    "tts": FAMILY_AUDIO,
    "safety": FAMILY_MODERATION,
}

_MODALITY_ALIASES = {
    "images": MODALITY_IMAGE,
    "img": MODALITY_IMAGE,
    "document": MODALITY_FILE,
    "documents": MODALITY_FILE,
    "files": MODALITY_FILE,
    "docs": MODALITY_FILE,
    "voice": MODALITY_AUDIO,
    "sound": MODALITY_AUDIO,
    "embeddings": MODALITY_EMBEDDING,
}

_CAPABILITY_ALIASES = {
    "tools": CAP_TOOL_CALL,
    "tool_calls": CAP_TOOL_CALL,
    "function_calling": CAP_TOOL_CALL,
    "functions": CAP_TOOL_CALL,
    "image_generate": CAP_IMAGE_GENERATION,
    "text_to_image": CAP_IMAGE_GENERATION,
    "text-to-image": CAP_IMAGE_GENERATION,
    "img2img": CAP_IMAGE_EDITING,
    "image_edit": CAP_IMAGE_EDITING,
    "image-editing": CAP_IMAGE_EDITING,
    "text_rendering": CAP_TEXT_RENDERING,
    "reasoning_effort": CAP_REASONING,
    "thinking": CAP_REASONING,
    "json": CAP_JSON_MODE,
    "structured_outputs": CAP_STRUCTURED_OUTPUT,
    "search": CAP_WEB_SEARCH,
}

_DETERMINISTIC_CONTROL_ALIASES = {
    "temp": CONTROL_TEMPERATURE,
    "topp": CONTROL_TOP_P,
    "top-p": CONTROL_TOP_P,
    "topk": CONTROL_TOP_K,
    "top-k": CONTROL_TOP_K,
    "version_pin": CONTROL_MODEL_VERSION_PIN,
    "model_pin": CONTROL_MODEL_VERSION_PIN,
    "strict_tool_schema": CONTROL_STRICT_SCHEMA,
    "json_schema": CONTROL_STRICT_SCHEMA,
    "tool_choice_required": CONTROL_TOOL_CHOICE,
    "system": CONTROL_SYSTEM_PROMPT,
    "system_message": CONTROL_SYSTEM_PROMPT,
    "cache": CONTROL_REQUEST_HASH_CACHE,
    "fingerprint": CONTROL_SYSTEM_FINGERPRINT,
}

_REASONING_CONTROL_ALIASES = {
    "message_directive": REASONING_CONTROL_MESSAGE_DIRECTIVE,
    "user_message_directive": REASONING_CONTROL_MESSAGE_DIRECTIVE,
    "think_directive": REASONING_CONTROL_MESSAGE_DIRECTIVE,
    "slash_think": REASONING_CONTROL_MESSAGE_DIRECTIVE,
    "system_directive": REASONING_CONTROL_SYSTEM_DIRECTIVE,
    "system_prompt_directive": REASONING_CONTROL_SYSTEM_DIRECTIVE,
    "template_kwarg": REASONING_CONTROL_TEMPLATE_KWARG,
    "chat_template_kwarg": REASONING_CONTROL_TEMPLATE_KWARG,
    "chat_template_kwargs": REASONING_CONTROL_TEMPLATE_KWARG,
    "enable_thinking": REASONING_CONTROL_TEMPLATE_KWARG,
    "native_bool": REASONING_CONTROL_NATIVE_BOOL,
    "think_bool": REASONING_CONTROL_NATIVE_BOOL,
    "thinking_bool": REASONING_CONTROL_NATIVE_BOOL,
    "structured_object": REASONING_CONTROL_STRUCTURED_OBJECT,
    "reasoning_object": REASONING_CONTROL_STRUCTURED_OBJECT,
    "thinking_budget": REASONING_CONTROL_BUDGET,
    "budget": REASONING_CONTROL_BUDGET,
    "effort": REASONING_CONTROL_EFFORT,
}

_REASONING_CONTROL_VALUE_ALIASES = {
    "enabled": REASONING_CONTROL_VALUE_ON,
    "enable": REASONING_CONTROL_VALUE_ON,
    "true": REASONING_CONTROL_VALUE_ON,
    "disabled": REASONING_CONTROL_VALUE_OFF,
    "disable": REASONING_CONTROL_VALUE_OFF,
    "false": REASONING_CONTROL_VALUE_OFF,
    "adaptive": REASONING_CONTROL_VALUE_AUTO,
    "automatic": REASONING_CONTROL_VALUE_AUTO,
    "dynamic": REASONING_CONTROL_VALUE_AUTO,
    "provider_auto": REASONING_CONTROL_VALUE_AUTO,
    "vendor_auto": REASONING_CONTROL_VALUE_AUTO,
}

_DEFAULT_TASK_BY_FAMILY = {
    FAMILY_CHAT: TASK_CHAT_COMPLETIONS,
    FAMILY_EMBEDDING: TASK_EMBEDDINGS_CREATE,
    FAMILY_IMAGE: TASK_IMAGE_GENERATE,
    FAMILY_VIDEO: TASK_VIDEO_GENERATE,
    FAMILY_AUDIO: TASK_AUDIO_TRANSCRIBE,
    FAMILY_RERANK: TASK_RERANK,
    FAMILY_CLASSIFICATION: TASK_CLASSIFY,
    FAMILY_MODERATION: TASK_MODERATE,
    FAMILY_UNKNOWN: TASK_UNKNOWN,
}

_DEFAULT_MODALITIES_BY_FAMILY = {
    FAMILY_CHAT: ((MODALITY_TEXT,), (MODALITY_TEXT,)),
    FAMILY_EMBEDDING: ((MODALITY_TEXT,), (MODALITY_EMBEDDING,)),
    FAMILY_IMAGE: ((MODALITY_TEXT,), (MODALITY_IMAGE,)),
    FAMILY_VIDEO: ((MODALITY_TEXT,), (MODALITY_VIDEO,)),
    FAMILY_AUDIO: ((MODALITY_TEXT,), (MODALITY_AUDIO,)),
    FAMILY_RERANK: ((MODALITY_TEXT,), (MODALITY_TEXT,)),
    FAMILY_CLASSIFICATION: ((MODALITY_TEXT,), (MODALITY_TEXT,)),
    FAMILY_MODERATION: ((MODALITY_TEXT,), (MODALITY_TEXT,)),
    FAMILY_UNKNOWN: ((), ()),
}

_DEFAULT_CAPABILITIES_BY_FAMILY = {
    FAMILY_IMAGE: (CAP_IMAGE_GENERATION,),
    FAMILY_VIDEO: (CAP_VIDEO_GENERATION,),
}


def _clean_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_choice(value: Any, allowed: frozenset[str], aliases: Mapping[str, str], default: str) -> str:
    token = _clean_token(value)
    token = aliases.get(token, token)
    return token if token in allowed else default


def normalize_family(value: Any) -> str:
    return _normalize_choice(value, FAMILIES, _FAMILY_ALIASES, FAMILY_UNKNOWN)


def normalize_source(value: Any) -> str:
    return _normalize_choice(value, SOURCES, {}, SOURCE_UNKNOWN)


def normalize_confidence(value: Any) -> str:
    return _normalize_choice(value, CONFIDENCES, {}, CONFIDENCE_UNKNOWN)


def normalize_modality(value: Any) -> str:
    return _normalize_choice(value, MODALITIES, _MODALITY_ALIASES, "")


def normalize_capability(value: Any) -> str:
    token = _clean_token(value)
    token = _CAPABILITY_ALIASES.get(token, token)
    return token if token in CAPABILITIES else ""


def normalize_assertion_status(value: Any) -> str:
    return _normalize_choice(value, ASSERTION_STATUSES, {}, ASSERTION_UNKNOWN)


def normalize_probe_status(value: Any) -> str:
    return _normalize_choice(value, PROBE_STATUSES, {}, "")


def normalize_deterministic_control(value: Any) -> str:
    token = _clean_token(value)
    token = _DETERMINISTIC_CONTROL_ALIASES.get(token, token)
    return token if token in DETERMINISTIC_CONTROLS else ""


def normalize_reasoning_control_mechanism(value: Any) -> str:
    token = _clean_token(value)
    token = _REASONING_CONTROL_ALIASES.get(token, token)
    return token if token in REASONING_CONTROL_MECHANISMS else ""


def normalize_reasoning_control_value(value: Any) -> str:
    token = _clean_token(value)
    token = _REASONING_CONTROL_VALUE_ALIASES.get(token, token)
    return token if token in REASONING_CONTROL_VALUES else ""


def _normalize_tokens(values: Any, normalizer) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, Mapping):
        values = [key for key, enabled in values.items() if enabled]
    elif isinstance(values, str) or not isinstance(values, Iterable):
        values = [values]
    out: list[str] = []
    for value in values:
        token = normalizer(value)
        if token and token not in out:
            out.append(token)
    return tuple(out)


def _normalize_limits(limits: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    if not isinstance(limits, Mapping):
        return ()
    return tuple(sorted((str(k), v) for k, v in limits.items() if str(k).strip()))


@dataclass(frozen=True)
class Modalities:
    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()

    @classmethod
    def from_values(cls, input: Any = None, output: Any = None) -> "Modalities":
        return cls(
            input=_normalize_tokens(input, normalize_modality),
            output=_normalize_tokens(output, normalize_modality),
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "input": list(self.input),
            "output": list(self.output),
        }


@dataclass(frozen=True)
class ModelCapability:
    family: str = FAMILY_UNKNOWN
    primary_task: str = TASK_UNKNOWN
    modalities: Modalities = field(default_factory=Modalities)
    capabilities: tuple[str, ...] = ()
    limits: tuple[tuple[str, Any], ...] = ()
    source: str = SOURCE_UNKNOWN
    confidence: str = CONFIDENCE_UNKNOWN

    @classmethod
    def build(
        cls,
        *,
        family: Any = FAMILY_UNKNOWN,
        primary_task: str | None = None,
        input_modalities: Any = None,
        output_modalities: Any = None,
        capabilities: Any = None,
        limits: Mapping[str, Any] | None = None,
        source: Any = SOURCE_UNKNOWN,
        confidence: Any = CONFIDENCE_UNKNOWN,
    ) -> "ModelCapability":
        normalized_family = normalize_family(family)
        default_input, default_output = _DEFAULT_MODALITIES_BY_FAMILY[normalized_family]
        return cls(
            family=normalized_family,
            primary_task=str(primary_task or _DEFAULT_TASK_BY_FAMILY[normalized_family]).strip() or TASK_UNKNOWN,
            modalities=Modalities.from_values(
                input_modalities if input_modalities is not None else default_input,
                output_modalities if output_modalities is not None else default_output,
            ),
            capabilities=_normalize_tokens(
                capabilities if capabilities is not None else _DEFAULT_CAPABILITIES_BY_FAMILY.get(normalized_family, ()),
                normalize_capability,
            ),
            limits=_normalize_limits(limits),
            source=normalize_source(source),
            confidence=normalize_confidence(confidence),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelCapability":
        if not isinstance(value, Mapping):
            return unknown_capability()
        modalities = value.get("modalities")
        if not isinstance(modalities, Mapping):
            modalities = {}
        return cls.build(
            family=value.get("family"),
            primary_task=value.get("primary_task"),
            input_modalities=modalities.get("input"),
            output_modalities=modalities.get("output"),
            capabilities=value.get("capabilities"),
            limits=value.get("limits"),
            source=value.get("source"),
            confidence=value.get("confidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "primary_task": self.primary_task,
            "modalities": self.modalities.to_dict(),
            "capabilities": list(self.capabilities),
            "limits": dict(self.limits),
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class CapabilityAssertion:
    capability: str = ""
    status: str = ASSERTION_UNKNOWN
    source: str = SOURCE_UNKNOWN
    confidence: str = CONFIDENCE_UNKNOWN
    evidence: tuple[tuple[str, Any], ...] = ()
    tested_at: str = ""

    @classmethod
    def build(
        cls,
        *,
        capability: Any,
        status: Any = ASSERTION_UNKNOWN,
        source: Any = SOURCE_UNKNOWN,
        confidence: Any = CONFIDENCE_UNKNOWN,
        evidence: Mapping[str, Any] | None = None,
        tested_at: Any = "",
    ) -> "CapabilityAssertion":
        normalized_capability = normalize_capability(capability)
        normalized_status = normalize_assertion_status(status)
        if not normalized_capability:
            normalized_status = ASSERTION_UNKNOWN
        return cls(
            capability=normalized_capability,
            status=normalized_status,
            source=normalize_source(source),
            confidence=normalize_confidence(confidence),
            evidence=_normalize_limits(evidence),
            tested_at=str(tested_at or "").strip(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityAssertion":
        if not isinstance(value, Mapping):
            return cls.build(capability="")
        return cls.build(
            capability=value.get("capability"),
            status=value.get("status"),
            source=value.get("source"),
            confidence=value.get("confidence"),
            evidence=value.get("evidence"),
            tested_at=value.get("tested_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
            "tested_at": self.tested_at,
        }


@dataclass(frozen=True)
class DeterministicControl:
    control: str = ""
    status: str = ASSERTION_UNKNOWN
    source: str = SOURCE_UNKNOWN
    confidence: str = CONFIDENCE_UNKNOWN
    evidence: tuple[tuple[str, Any], ...] = ()
    tested_at: str = ""

    @classmethod
    def build(
        cls,
        *,
        control: Any,
        status: Any = ASSERTION_UNKNOWN,
        source: Any = SOURCE_UNKNOWN,
        confidence: Any = CONFIDENCE_UNKNOWN,
        evidence: Mapping[str, Any] | None = None,
        tested_at: Any = "",
    ) -> "DeterministicControl":
        normalized_control = normalize_deterministic_control(control)
        normalized_status = normalize_assertion_status(status)
        if not normalized_control:
            normalized_status = ASSERTION_UNKNOWN
        return cls(
            control=normalized_control,
            status=normalized_status,
            source=normalize_source(source),
            confidence=normalize_confidence(confidence),
            evidence=_normalize_limits(evidence),
            tested_at=str(tested_at or "").strip(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeterministicControl":
        if not isinstance(value, Mapping):
            return cls.build(control="")
        return cls.build(
            control=value.get("control"),
            status=value.get("status"),
            source=value.get("source"),
            confidence=value.get("confidence"),
            evidence=value.get("evidence"),
            tested_at=value.get("tested_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
            "tested_at": self.tested_at,
        }


@dataclass(frozen=True)
class CapabilityProbeResult:
    provider: str
    model_id: str
    capability: str
    status: str
    tested_at: str = ""
    endpoint_id: str = ""
    stable_model_id: str = ""
    request_hash: str = ""
    response_id: str = ""
    response_fingerprint: str = ""
    evidence: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def build(
        cls,
        *,
        provider: Any,
        model_id: Any,
        capability: Any,
        status: Any,
        tested_at: Any = "",
        endpoint_id: Any = "",
        stable_model_id: Any = "",
        request_hash: Any = "",
        response_id: Any = "",
        response_fingerprint: Any = "",
        evidence: Mapping[str, Any] | None = None,
    ) -> "CapabilityProbeResult":
        normalized_capability = normalize_capability(capability)
        normalized_status = normalize_probe_status(status)
        if not normalized_capability or not normalized_status:
            normalized_status = PROBE_FAIL
        return cls(
            provider=str(provider or "").strip(),
            model_id=str(model_id or "").strip(),
            capability=normalized_capability,
            status=normalized_status,
            tested_at=str(tested_at or "").strip(),
            endpoint_id=str(endpoint_id or "").strip(),
            stable_model_id=str(stable_model_id or "").strip(),
            request_hash=str(request_hash or "").strip(),
            response_id=str(response_id or "").strip(),
            response_fingerprint=str(response_fingerprint or "").strip(),
            evidence=_normalize_limits(evidence),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityProbeResult":
        if not isinstance(value, Mapping):
            return cls.build(provider="", model_id="", capability="", status=PROBE_FAIL)
        return cls.build(
            provider=value.get("provider"),
            endpoint_id=value.get("endpoint_id"),
            model_id=value.get("model_id"),
            stable_model_id=value.get("stable_model_id"),
            capability=value.get("capability"),
            status=value.get("status"),
            tested_at=value.get("tested_at"),
            request_hash=value.get("request_hash"),
            response_id=value.get("response_id"),
            response_fingerprint=value.get("response_fingerprint"),
            evidence=value.get("evidence"),
        )

    def to_assertion(self) -> CapabilityAssertion:
        status_map = {
            PROBE_PASS: ASSERTION_VERIFIED,
            PROBE_FAIL: ASSERTION_UNSUPPORTED,
            PROBE_PARTIAL: ASSERTION_CLAIMED,
        }
        return CapabilityAssertion.build(
            capability=self.capability,
            status=status_map.get(self.status, ASSERTION_UNKNOWN),
            source=SOURCE_CAPABILITY_PROBE,
            confidence=CONFIDENCE_EXPLICIT if self.status == PROBE_PASS else CONFIDENCE_HEURISTIC,
            evidence={
                "provider": self.provider,
                "endpoint_id": self.endpoint_id,
                "model_id": self.model_id,
                "stable_model_id": self.stable_model_id,
                "request_hash": self.request_hash,
                "response_id": self.response_id,
                "response_fingerprint": self.response_fingerprint,
                **dict(self.evidence),
            },
            tested_at=self.tested_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint_id": self.endpoint_id,
            "model_id": self.model_id,
            "stable_model_id": self.stable_model_id,
            "capability": self.capability,
            "status": self.status,
            "tested_at": self.tested_at,
            "request_hash": self.request_hash,
            "response_id": self.response_id,
            "response_fingerprint": self.response_fingerprint,
            "evidence": dict(self.evidence),
        }


def capability_assertions_from_capability(
    capability: ModelCapability,
    *,
    status: str = ASSERTION_CLAIMED,
    source: str | None = None,
    confidence: str | None = None,
) -> tuple[CapabilityAssertion, ...]:
    return tuple(
        CapabilityAssertion.build(
            capability=cap,
            status=status,
            source=source or capability.source,
            confidence=confidence or capability.confidence,
        )
        for cap in capability.capabilities
    )


def deterministic_controls_from_values(
    values: Any,
    *,
    status: str = ASSERTION_CLAIMED,
    source: str = SOURCE_PROVIDER_READER,
    confidence: str = CONFIDENCE_PROVIDER_REPORTED,
) -> tuple[DeterministicControl, ...]:
    return tuple(
        DeterministicControl.build(
            control=control,
            status=status,
            source=source,
            confidence=confidence,
        )
        for control in _normalize_tokens(values, normalize_deterministic_control)
    )


@dataclass(frozen=True)
class CapabilityQuery:
    surface: str
    families: tuple[str, ...] = ()
    primary_tasks: tuple[str, ...] = ()
    input_all: tuple[str, ...] = ()
    input_any: tuple[str, ...] = ()
    output_all: tuple[str, ...] = ()
    output_any: tuple[str, ...] = ()
    modality_any: tuple[str, ...] = ()
    capabilities_all: tuple[str, ...] = ()
    capabilities_any: tuple[str, ...] = ()

    def matches(self, capability: ModelCapability) -> bool:
        input_set = set(capability.modalities.input)
        output_set = set(capability.modalities.output)
        modality_set = input_set | output_set
        cap_set = set(capability.capabilities)
        if self.families and capability.family not in self.families:
            return False
        if self.primary_tasks and capability.primary_task not in self.primary_tasks:
            return False
        if self.input_all and not set(self.input_all).issubset(input_set):
            return False
        if self.input_any and input_set.isdisjoint(self.input_any):
            return False
        if self.output_all and not set(self.output_all).issubset(output_set):
            return False
        if self.output_any and output_set.isdisjoint(self.output_any):
            return False
        if self.modality_any and modality_set.isdisjoint(self.modality_any):
            return False
        if self.capabilities_all and not set(self.capabilities_all).issubset(cap_set):
            return False
        if self.capabilities_any and cap_set.isdisjoint(self.capabilities_any):
            return False
        return True


DISPLAY_QUERIES = (
    CapabilityQuery(
        surface="chat",
        families=(FAMILY_CHAT,),
        input_all=(MODALITY_TEXT,),
        output_all=(MODALITY_TEXT,),
    ),
    CapabilityQuery(
        surface="vision_chat",
        families=(FAMILY_CHAT,),
        input_all=(MODALITY_TEXT, MODALITY_IMAGE),
        output_all=(MODALITY_TEXT,),
    ),
    CapabilityQuery(
        surface="document_chat",
        families=(FAMILY_CHAT,),
        input_all=(MODALITY_TEXT,),
        input_any=(MODALITY_FILE, MODALITY_PDF),
        output_all=(MODALITY_TEXT,),
    ),
    CapabilityQuery(
        surface="image_generation",
        families=(FAMILY_IMAGE,),
        output_all=(MODALITY_IMAGE,),
        capabilities_all=(CAP_IMAGE_GENERATION,),
    ),
    CapabilityQuery(
        surface="image_editing",
        families=(FAMILY_IMAGE,),
        input_all=(MODALITY_IMAGE,),
        output_all=(MODALITY_IMAGE,),
        capabilities_any=(CAP_IMAGE_EDITING, CAP_INPAINTING),
    ),
    CapabilityQuery(
        surface="video_generation",
        families=(FAMILY_VIDEO,),
        output_all=(MODALITY_VIDEO,),
        capabilities_all=(CAP_VIDEO_GENERATION,),
    ),
    CapabilityQuery(
        surface="audio_realtime",
        families=(FAMILY_AUDIO,),
        modality_any=(MODALITY_AUDIO,),
        capabilities_any=(CAP_AUDIO_INPUT, CAP_AUDIO_OUTPUT, CAP_TRANSCRIPTION, CAP_TTS, CAP_REALTIME),
    ),
    CapabilityQuery(
        surface="embeddings",
        families=(FAMILY_EMBEDDING,),
        output_all=(MODALITY_EMBEDDING,),
    ),
    CapabilityQuery(
        surface="rerank_scoring",
        families=(FAMILY_RERANK,),
    ),
    CapabilityQuery(
        surface="moderation_classification",
        families=(FAMILY_MODERATION, FAMILY_CLASSIFICATION),
    ),
)


def display_surfaces_for(capability: ModelCapability) -> tuple[str, ...]:
    return tuple(query.surface for query in DISPLAY_QUERIES if query.matches(capability))


def unknown_capability(
    *,
    source: str = SOURCE_UNKNOWN,
    confidence: str = CONFIDENCE_UNKNOWN,
) -> ModelCapability:
    return ModelCapability.build(source=source, confidence=confidence)


def capability_from_endpoint_type(model_type: Any) -> ModelCapability:
    """Return capability metadata from an explicit endpoint model type.

    Missing or unknown endpoint types remain unknown here. Runtime compatibility
    may still treat legacy rows as chat-capable, but this schema layer should
    not turn absence of evidence into model capability truth.
    """
    token = _clean_token(model_type)
    if token == "llm":
        return ModelCapability.build(
            family=FAMILY_CHAT,
            source=SOURCE_ENDPOINT_CONFIG,
            confidence=CONFIDENCE_EXPLICIT,
        )
    if token == "image":
        return ModelCapability.build(
            family=FAMILY_IMAGE,
            source=SOURCE_ENDPOINT_CONFIG,
            confidence=CONFIDENCE_EXPLICIT,
        )
    return unknown_capability(source=SOURCE_ENDPOINT_CONFIG)
