"""Shared model capability classification helpers."""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


FAMILY_UNKNOWN = "unknown"
FAMILY_CHAT = "chat"
FAMILY_IMAGE = "image"
FAMILY_AUDIO = "audio"
FAMILY_VIDEO = "video"
FAMILY_EMBEDDING = "embedding"
FAMILY_RERANK = "rerank"
FAMILY_MODERATION = "moderation"
FAMILY_CLASSIFICATION = "classification"

TASK_UNKNOWN = "unknown"

MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"
MODALITY_FILE = "file"
MODALITY_PDF = "pdf"
MODALITY_EMBEDDING = "embedding"

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

CAPABILITIES = frozenset({
    CAP_VISION, CAP_FILES, CAP_PDF, CAP_AUDIO_INPUT, CAP_AUDIO_OUTPUT,
    CAP_IMAGE_GENERATION, CAP_IMAGE_EDITING, CAP_REASONING, CAP_TOOL_CALL,
    CAP_STRUCTURED_OUTPUT, CAP_WEB_SEARCH, CAP_STREAMING, CAP_JSON_MODE,
    CAP_TRANSCRIPTION, CAP_TTS, CAP_REALTIME, CAP_TEXT_RENDERING,
})

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


_RESPONSES_REQUIRED_MODEL_RE = re.compile(
    r"^(?:"
    r"o[13]-pro(?:-\d{4}-\d{2}-\d{2})?|"
    r"gpt-5(?:\.\d+)?-pro(?:-\d{4}-\d{2}-\d{2})?|"
    r"gpt-5(?:\.\d+)?-codex(?:-max)?(?:-\d{4}-\d{2}-\d{2})?"
    r")$",
    re.IGNORECASE,
)


def _host_match(url: str, *domains: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def is_openai_responses_required_model(model: str) -> bool:
    model_id = str(model or "").strip().split("/")[-1]
    return bool(model_id and _RESPONSES_REQUIRED_MODEL_RE.match(model_id))


def requires_openai_responses_api(url: str, model: str) -> bool:
    return _host_match(url, "openai.com") and is_openai_responses_required_model(model)


# ---------------------------------------------------------------------------
# ModelCapability
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelModalities:
    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelCapability:
    family: str = FAMILY_UNKNOWN
    primary_task: str = TASK_UNKNOWN
    modalities: ModelModalities = field(default_factory=ModelModalities)
    capabilities: tuple[str, ...] = ()
    limits: tuple[tuple[str, Any], ...] = ()
    source: str = SOURCE_UNKNOWN
    confidence: str = CONFIDENCE_UNKNOWN

    @classmethod
    def build(cls, *, family=FAMILY_UNKNOWN, primary_task=None, modalities=None,
              input_modalities=None, output_modalities=None,
              capabilities=None, limits=None, source=SOURCE_UNKNOWN,
              confidence=CONFIDENCE_UNKNOWN) -> "ModelCapability":
        if input_modalities is not None or output_modalities is not None:
            modalities = ModelModalities(
                input=tuple(input_modalities or ()),
                output=tuple(output_modalities or ())
            )
        elif modalities is None:
            modalities = ModelModalities()
        elif isinstance(modalities, (list, tuple)):
            modalities = ModelModalities(input=tuple(modalities))
        
        if isinstance(limits, dict):
            limits = tuple(limits.items())
        else:
            limits = tuple(limits or ())

        return cls(
            family=family,
            primary_task=primary_task or TASK_UNKNOWN,
            modalities=modalities,
            capabilities=tuple(capabilities or ()),
            limits=limits,
            source=source,
            confidence=confidence,
        )

    @classmethod
    def from_dict(cls, value) -> "ModelCapability":
        if not isinstance(value, dict):
            return cls()
        mods = value.get("modalities")
        if isinstance(mods, dict):
            modalities = ModelModalities(
                input=tuple(mods.get("input", ())),
                output=tuple(mods.get("output", ()))
            )
        elif isinstance(mods, (list, tuple)):
            modalities = ModelModalities(input=tuple(mods))
        else:
            modalities = ModelModalities()
        return cls(
            family=str(value.get("family", FAMILY_UNKNOWN)),
            primary_task=str(value.get("primary_task", TASK_UNKNOWN)),
            modalities=modalities,
            capabilities=tuple(value.get("capabilities", ())),
            limits=tuple(tuple(p) for p in value.get("limits", ())),
            source=str(value.get("source", SOURCE_UNKNOWN)),
            confidence=str(value.get("confidence", CONFIDENCE_UNKNOWN)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "primary_task": self.primary_task,
            "modalities": {
                "input": list(self.modalities.input),
                "output": list(self.modalities.output),
            },
            "capabilities": list(self.capabilities),
            "limits": [list(p) for p in self.limits],
            "source": self.source,
            "confidence": self.confidence,
        }


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


def unknown_capability(*, source: str = SOURCE_UNKNOWN, confidence: str = CONFIDENCE_UNKNOWN) -> ModelCapability:
    return ModelCapability.build(source=source, confidence=confidence)


def normalize_modality(value: str) -> str:
    val = str(value or "").strip().lower()
    valid = {
        MODALITY_TEXT,
        MODALITY_IMAGE,
        MODALITY_AUDIO,
        MODALITY_VIDEO,
        MODALITY_FILE,
        MODALITY_PDF,
        MODALITY_EMBEDDING,
    }
    if val in valid:
        return val
    return ""


def normalize_capability(value: str) -> str:
    val = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "vision": CAP_VISION,
        "files": CAP_FILES,
        "pdf": CAP_PDF,
        "audio_input": CAP_AUDIO_INPUT,
        "audio_output": CAP_AUDIO_OUTPUT,
        "image_generation": CAP_IMAGE_GENERATION,
        "image_editing": CAP_IMAGE_EDITING,
        "reasoning": CAP_REASONING,
        "tool_call": CAP_TOOL_CALL,
        "tools": CAP_TOOL_CALL,
        "structured_output": CAP_STRUCTURED_OUTPUT,
        "web_search": CAP_WEB_SEARCH,
        "streaming": CAP_STREAMING,
        "json_mode": CAP_JSON_MODE,
        "transcription": CAP_TRANSCRIPTION,
        "tts": CAP_TTS,
        "realtime": CAP_REALTIME,
        "text_rendering": CAP_TEXT_RENDERING,
    }
    val = aliases.get(val, val)
    if val in CAPABILITIES:
        return val
    return ""


# ---------------------------------------------------------------------------
# DeterministicControl
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeterministicControl:
    control: str
    status: str = ASSERTION_UNKNOWN
    source: str = SOURCE_UNKNOWN
    confidence: str = CONFIDENCE_UNKNOWN
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "details": self.details,
        }


def _to_list(values: Any) -> list[str]:
    if isinstance(values, str):
        return [values]
    if isinstance(values, bytes):
        return [values.decode()]
    try:
        return [str(v) for v in values]
    except TypeError:
        return [str(values)]


def deterministic_controls_from_values(
    values: Any,
    status: str = ASSERTION_UNKNOWN,
    source: str = SOURCE_UNKNOWN,
    confidence: str = CONFIDENCE_UNKNOWN,
) -> tuple[DeterministicControl, ...]:
    controls = []
    aliases = {
        "temperature": CONTROL_TEMPERATURE,
        "top_p": CONTROL_TOP_P,
        "top_k": CONTROL_TOP_K,
        "seed": CONTROL_SEED,
        "model_version_pin": CONTROL_MODEL_VERSION_PIN,
        "strict_schema": CONTROL_STRICT_SCHEMA,
        "tool_choice": CONTROL_TOOL_CHOICE,
        "system_prompt": CONTROL_SYSTEM_PROMPT,
        "prompt_caching": CONTROL_PROMPT_CACHING,
        "batch": CONTROL_BATCH,
        "request_hash_cache": CONTROL_REQUEST_HASH_CACHE,
        "system_fingerprint": CONTROL_SYSTEM_FINGERPRINT,
    }
    for v in _to_list(values):
        val = str(v or "").strip().lower().replace("-", "_")
        mapped = aliases.get(val, val)
        if mapped in DETERMINISTIC_CONTROLS:
            controls.append(DeterministicControl(control=mapped, status=status, source=source, confidence=confidence))
    return tuple(controls)


# ---------------------------------------------------------------------------
# CapabilityAssertion
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityAssertion:
    capability: str
    status: str = ASSERTION_UNKNOWN
    source: str = SOURCE_UNKNOWN
    confidence: str = CONFIDENCE_UNKNOWN
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(cls, *, capability="", status=ASSERTION_UNKNOWN, source=SOURCE_UNKNOWN,
              confidence=CONFIDENCE_UNKNOWN, evidence=None) -> "CapabilityAssertion":
        details = {"evidence": evidence} if evidence else {}
        return cls(capability=capability, status=status, source=source, confidence=confidence, details=details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "details": self.details,
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


__all__ = [
    "is_openai_responses_required_model",
    "requires_openai_responses_api",
    "ModelCapability",
    "unknown_capability",
    "DeterministicControl",
    "deterministic_controls_from_values",
    "CapabilityAssertion",
    "capability_assertions_from_capability",
    "normalize_modality",
    "normalize_capability",
]
