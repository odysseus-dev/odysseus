"""Google AI Studio / Gemini native Models API capability mapping.

This module maps already-fetched `models.list` and `models.get` payloads into
Odysseus' canonical model capability shape. It performs no network I/O and
does not infer model capabilities from model IDs, display names, or product
families. Only fields explicitly returned by Google's Model resource are
mapped here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import as_list, compact_str, int_limit


METHOD_GENERATE_CONTENT = "generateContent"
METHOD_GENERATE_MESSAGE = "generateMessage"
METHOD_GENERATE_TEXT = "generateText"
METHOD_GENERATE_ANSWER = "generateAnswer"
METHOD_EMBED_CONTENT = "embedContent"
METHOD_ASYNC_BATCH_EMBED = "asyncBatchEmbedContent"
METHOD_PREDICT = "predict"
METHOD_PREDICT_LONG_RUNNING = "predictLongRunning"
METHOD_BATCH_GENERATE = "batchGenerateContent"
METHOD_CREATE_CACHED_CONTENT = "createCachedContent"

TEXT_GENERATION_METHODS = frozenset(
    {
        METHOD_GENERATE_CONTENT,
        METHOD_GENERATE_MESSAGE,
        METHOD_GENERATE_TEXT,
        METHOD_GENERATE_ANSWER,
    }
)
EMBEDDING_METHODS = frozenset({METHOD_EMBED_CONTENT, METHOD_ASYNC_BATCH_EMBED})
BATCH_METHODS = frozenset({METHOD_BATCH_GENERATE, METHOD_ASYNC_BATCH_EMBED})

MODEL_FIELD_MAP = {
    "name": "vendor resource name",
    "baseModelId": "vendor model id",
    "displayName": "display name",
    "description": "display description only",
    "inputTokenLimit": "limits.input_tokens and limits.context_tokens",
    "outputTokenLimit": "limits.output_tokens",
    "supportedGenerationMethods": "provider method support signal",
    "thinking": "capabilities.reasoning when true",
    "temperature": "deterministic_controls.temperature when present",
    "maxTemperature": "deterministic_controls.temperature when present",
    "topP": "deterministic_controls.top_p when present",
    "topK": "deterministic_controls.top_k when present",
}


def google_model_id(raw: Mapping[str, Any]) -> str:
    value = compact_str(raw.get("baseModelId")) or compact_str(raw.get("name"))
    return value.removeprefix("models/")


def supported_methods(raw: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(compact_str(method) for method in as_list(raw.get("supportedGenerationMethods")) if method)


def limits_from_model(raw: Mapping[str, Any]) -> dict[str, Any]:
    limits: dict[str, Any] = {}
    input_limit = int_limit(raw.get("inputTokenLimit"))
    output_limit = int_limit(raw.get("outputTokenLimit"))
    if input_limit:
        limits["input_tokens"] = input_limit
        limits["context_tokens"] = input_limit
    if output_limit:
        limits["output_tokens"] = output_limit
    return limits


def _capability(
    *,
    family: str,
    input_modalities: tuple[str, ...],
    output_modalities: tuple[str, ...],
    capabilities: tuple[str, ...] = (),
    limits: Mapping[str, Any] | None = None,
    primary_task: str | None = None,
    source: str = mc.SOURCE_PROVIDER_READER,
    confidence: str = mc.CONFIDENCE_PROVIDER_REPORTED,
) -> mc.ModelCapability:
    return mc.ModelCapability.build(
        family=family,
        primary_task=primary_task,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        capabilities=capabilities,
        limits=limits,
        source=source,
        confidence=confidence,
    )


def capability_from_model(raw: Mapping[str, Any]) -> mc.ModelCapability:
    methods = supported_methods(raw)
    capabilities: list[str] = []
    if raw.get("thinking") is True:
        capabilities.append(mc.CAP_REASONING)

    if methods & EMBEDDING_METHODS and not methods & TEXT_GENERATION_METHODS:
        return _capability(
            family=mc.FAMILY_EMBEDDING,
            input_modalities=(mc.MODALITY_TEXT,),
            output_modalities=(mc.MODALITY_EMBEDDING,),
            capabilities=tuple(capabilities),
            limits=limits_from_model(raw),
        )

    # `generateContent` proves the model supports Google's content generation
    # method, but the Model resource does not expose input/output modalities.
    # Keep the model unknown instead of guessing chat/image/audio/video from ID.
    if methods & TEXT_GENERATION_METHODS:
        return _capability(
            family=mc.FAMILY_UNKNOWN,
            input_modalities=(),
            output_modalities=(),
            capabilities=tuple(capabilities),
            limits=limits_from_model(raw),
        )

    capability = mc.unknown_capability(
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_UNKNOWN,
    )
    limits = limits_from_model(raw)
    if limits or capabilities:
        return _capability(
            family=mc.FAMILY_UNKNOWN,
            input_modalities=(),
            output_modalities=(),
            capabilities=tuple(capabilities),
            limits=limits,
        )
    return capability


def deterministic_controls_from_model(raw: Mapping[str, Any]) -> tuple[mc.DeterministicControl, ...]:
    methods = supported_methods(raw)
    controls: list[str] = []
    if "temperature" in raw or "maxTemperature" in raw:
        controls.append(mc.CONTROL_TEMPERATURE)
    if "topP" in raw:
        controls.append(mc.CONTROL_TOP_P)
    if raw.get("topK") not in (None, ""):
        controls.append(mc.CONTROL_TOP_K)
    if METHOD_CREATE_CACHED_CONTENT in methods:
        controls.append(mc.CONTROL_PROMPT_CACHING)
    if methods & BATCH_METHODS:
        controls.append(mc.CONTROL_BATCH)
    return mc.deterministic_controls_from_values(
        controls,
        status=mc.ASSERTION_CLAIMED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
    )
