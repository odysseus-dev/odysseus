"""Shared helpers for vendor-specific model capability readers.

Readers in this package normalize already-fetched provider payload shapes and
explicit provider fields. They do not perform network I/O and must not infer
authoritative capability from model IDs, names, display names, or ownership
labels.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from src import model_capabilities as mc


VENDOR_GENERIC_OPENAI = "generic_openai"
VENDOR_OPENAI = "openai"
VENDOR_OPENROUTER = "openrouter"
VENDOR_GOOGLE = "google"
VENDOR_ANTHROPIC = "anthropic"
VENDOR_OLLAMA = "ollama"
VENDOR_LMSTUDIO = "lmstudio"
VENDOR_LLAMACPP = "llamacpp"
VENDOR_VLLM = "vllm"
VENDOR_SGLANG = "sglang"
VENDOR_HUGGINGFACE = "huggingface"
VENDOR_MISTRAL = "mistral"
VENDOR_COPILOT = "copilot"
VENDOR_CHATGPT_SUBSCRIPTION = "chatgpt_subscription"
VENDOR_COHERE = "cohere"
VENDOR_MINIMAX = "minimax"
VENDOR_MOONSHOT = "moonshot"
VENDOR_GROQ = "groq"
VENDOR_NVIDIA = "nvidia"
VENDOR_CEREBRAS = "cerebras"
VENDOR_DEEPSEEK = "deepseek"
VENDOR_TOGETHER = "together"
VENDOR_FIREWORKS = "fireworks"
VENDOR_XAI = "xai"
VENDOR_ZAI = "zai"
VENDOR_UNKNOWN = "unknown"

CANONICAL_MODEL_SHAPE_VERSION = 1
CANONICAL_RUNTIME_CONTEXT_SHAPE_VERSION = 1


@dataclass(frozen=True)
class ModelCapabilityRecord:
    vendor: str
    model_id: str
    capability: mc.ModelCapability
    display_name: str = ""
    stable_model_id: str = ""
    capability_assertions: tuple[mc.CapabilityAssertion, ...] = ()
    deterministic_controls: tuple[mc.DeterministicControl, ...] = ()
    provider_source: str = "unknown"
    catalog_shape_id: str = ""
    fallback: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.stable_model_id:
            object.__setattr__(self, "stable_model_id", stable_model_id_for(self.vendor, self.model_id))
        if not self.capability_assertions and self.capability.capabilities:
            object.__setattr__(
                self,
                "capability_assertions",
                mc.capability_assertions_from_capability(
                    self.capability,
                    status=mc.ASSERTION_CLAIMED,
                    source=self.capability.source,
                    confidence=self.capability.confidence,
                ),
            )

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        controls = tuple(
            dict.fromkeys(
                control.control
                for control in self.deterministic_controls
                if control.control
            )
        )
        data = {
            "schema_version": CANONICAL_MODEL_SHAPE_VERSION,
            "provider": self.vendor,
            "model": self.model_id,
            "stable_id": self.stable_model_id,
            "family": self.capability.family,
            "task": self.capability.primary_task,
            "modalities": self.capability.modalities.to_dict(),
            "features": list(self.capability.capabilities),
            "limits": dict(self.capability.limits),
            "controls": list(controls),
            "evidence": {
                "source": self.capability.source,
                "confidence": self.capability.confidence,
                "provider_source": self.provider_source,
                "shape": self.catalog_shape_id,
                "fallback": self.fallback,
            },
        }
        if include_raw:
            data["raw"] = dict(self.raw)
        return data


@dataclass(frozen=True)
class RuntimeContextAllocationRecord:
    """Provider-reported context allocated to one currently loaded model.

    This runtime state is deliberately separate from ``ModelCapability.limits``:
    a model's maximum, configured ``num_ctx``, and the allocation reported for a
    loaded process are different facts with different lifetimes.
    """

    vendor: str
    model_id: str
    allocated_context_tokens: int
    stable_model_id: str = ""
    source: str = mc.SOURCE_PROVIDER_READER
    confidence: str = mc.CONFIDENCE_PROVIDER_REPORTED
    runtime_shape_id: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        context_tokens = int_limit(self.allocated_context_tokens)
        if context_tokens is None:
            raise ValueError("allocated_context_tokens must be a positive integer")
        object.__setattr__(self, "allocated_context_tokens", context_tokens)
        if not self.stable_model_id:
            object.__setattr__(self, "stable_model_id", stable_model_id_for(self.vendor, self.model_id))

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data = {
            "schema_version": CANONICAL_RUNTIME_CONTEXT_SHAPE_VERSION,
            "provider": self.vendor,
            "model": self.model_id,
            "stable_id": self.stable_model_id,
            "runtime": {
                "allocated_context_tokens": self.allocated_context_tokens,
            },
            "evidence": {
                "source": self.source,
                "confidence": self.confidence,
                "shape": self.runtime_shape_id,
                "scope": "loaded_model",
            },
        }
        if include_raw:
            data["raw"] = dict(self.raw)
        return data


class CapabilityReader(Protocol):
    vendor: str

    def records_from_payload(
        self,
        payload: Any,
        *,
        endpoint_id: Any = "",
        base_url: Any = "",
    ) -> tuple[ModelCapabilityRecord, ...]:
        """Normalize a provider model-list payload into capability records."""


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def compact_str(value: Any) -> str:
    return str(value or "").strip()


def identity_str(value: Any) -> str:
    """Return a provider identity only when the payload supplied a string."""

    return value.strip() if isinstance(value, str) else ""


def _identity_part(value: Any) -> str:
    text = compact_str(value).lower()
    out = []
    for char in text:
        out.append(char if char.isalnum() or char in {"-", "_", ".", "/", ":"} else "_")
    return "".join(out).strip("_") or "unknown"


def _base_url_scope(base_url: Any) -> str:
    parsed = urlparse(compact_str(base_url))
    if not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    normalized = f"{parsed.scheme or 'http'}://{parsed.hostname.lower()}{port}{path}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"url:{digest}"


def stable_model_id_for(vendor: Any, model_id: Any, *, endpoint_id: Any = "", base_url: Any = "") -> str:
    vendor_part = _identity_part(vendor or VENDOR_UNKNOWN)
    model_part = _identity_part(model_id)
    endpoint = compact_str(endpoint_id)
    if endpoint:
        scope = f"endpoint:{_identity_part(endpoint)}"
    else:
        scope = _base_url_scope(base_url) or "global"
    return f"{vendor_part}|{scope}|{model_part}"


def model_id_from(raw: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = identity_str(raw.get(key))
        if value:
            return value.removeprefix("models/")
    return ""


def int_limit(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        return None
    try:
        limit = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def merge_unique(*groups: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for group in groups:
        for value in group:
            token = compact_str(value)
            if token and token not in out:
                out.append(token)
    return tuple(out)


def deterministic_controls_from_supported_parameters(values: Any) -> tuple[mc.DeterministicControl, ...]:
    return mc.deterministic_controls_from_values(
        values,
        status=mc.ASSERTION_CLAIMED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
    )


def openai_model_items(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, (list, tuple)):
        data = payload
    else:
        payload = as_mapping(payload)
        data = payload.get("data")
        if data is None:
            data = payload.get("models")
    return tuple(item for item in as_list(data) if isinstance(item, Mapping))


def normalize_modality_token(value: Any) -> str:
    token = compact_str(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "txt": mc.MODALITY_TEXT,
        "textual": mc.MODALITY_TEXT,
        "image_url": mc.MODALITY_IMAGE,
        "images": mc.MODALITY_IMAGE,
        "img": mc.MODALITY_IMAGE,
        "audio_url": mc.MODALITY_AUDIO,
        "speech": mc.MODALITY_AUDIO,
        "documents": mc.MODALITY_FILE,
        "document": mc.MODALITY_FILE,
        "files": mc.MODALITY_FILE,
        "file_search": mc.MODALITY_FILE,
        "pdfs": mc.MODALITY_PDF,
        "embeddings": mc.MODALITY_EMBEDDING,
    }
    token = aliases.get(token, token)
    return mc.normalize_modality(token)


def modalities_from_value(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = value.replace(",", "+").replace("/", "+").split("+")
    else:
        parts = as_list(value)
    out: list[str] = []
    for part in parts:
        token = normalize_modality_token(part)
        if token and token not in out:
            out.append(token)
    return tuple(out)


def split_modality_arrow(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    text = compact_str(value).lower()
    if not text:
        return (), ()
    for arrow in ("->", "=>", "to"):
        if arrow in text:
            left, right = text.split(arrow, 1)
            return modalities_from_value(left), modalities_from_value(right)
    return modalities_from_value(text), ()


def family_from_modalities(input_modalities: Iterable[str], output_modalities: Iterable[str]) -> str:
    output_set = set(output_modalities)
    if mc.MODALITY_EMBEDDING in output_set:
        return mc.FAMILY_EMBEDDING
    if mc.MODALITY_IMAGE in output_set:
        return mc.FAMILY_IMAGE
    if mc.MODALITY_VIDEO in output_set:
        return mc.FAMILY_VIDEO
    if mc.MODALITY_AUDIO in output_set:
        return mc.FAMILY_AUDIO
    if mc.MODALITY_TEXT in output_set:
        return mc.FAMILY_CHAT
    return mc.FAMILY_UNKNOWN


def primary_task_for_family(family: str, capabilities: Iterable[str] = ()) -> str | None:
    caps = set(capabilities)
    if family == mc.FAMILY_IMAGE and (mc.CAP_IMAGE_EDITING in caps or mc.CAP_INPAINTING in caps):
        return mc.TASK_IMAGE_EDIT
    if family == mc.FAMILY_AUDIO and mc.CAP_TTS in caps:
        return mc.TASK_AUDIO_SYNTHESIZE
    if family == mc.FAMILY_AUDIO and mc.CAP_TRANSCRIPTION in caps:
        return mc.TASK_AUDIO_TRANSCRIBE
    return None


def build_capability(
    *,
    family: str,
    input_modalities: Iterable[str] = (),
    output_modalities: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    limits: Mapping[str, Any] | None = None,
    source: str = mc.SOURCE_PROVIDER_READER,
    confidence: str = mc.CONFIDENCE_PROVIDER_REPORTED,
) -> mc.ModelCapability:
    return mc.ModelCapability.build(
        family=family,
        primary_task=primary_task_for_family(family, capabilities),
        input_modalities=tuple(input_modalities),
        output_modalities=tuple(output_modalities),
        capabilities=tuple(capabilities),
        limits=limits,
        source=source,
        confidence=confidence,
    )


def detect_vendor(base_url: Any = "", endpoint_kind: Any = "") -> str:
    # Import lazily to keep the reader primitives independent of registry load
    # order.  Exact endpoint kind and host identity are authoritative enough
    # for provider selection; default ports are not.
    from src import provider_capability_schemas as pcs

    resolution = pcs.resolve_provider(
        endpoint_kind=endpoint_kind,
        base_url=base_url,
    )
    if resolution.provider_id != pcs.PROVIDER_UNKNOWN:
        return resolution.provider_id
    return VENDOR_UNKNOWN
