"""General structural reader for OpenAI-compatible model-list payloads.

Identity-only model cards remain unknown.  Rich records are promoted only from
recognized explicit fields (modalities, task/type, capability booleans,
supported parameters, and numeric limits).  Names and descriptions are never
parsed for capability hints.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_GENERIC_OPENAI,
    as_list,
    as_mapping,
    build_capability,
    compact_str,
    deterministic_controls_from_supported_parameters,
    family_from_modalities,
    int_limit,
    merge_unique,
    model_id_from,
    modalities_from_value,
    openai_model_items,
    split_modality_arrow,
    stable_model_id_for,
)


vendor = VENDOR_GENERIC_OPENAI


_TYPE_FAMILIES = {
    "llm": mc.FAMILY_CHAT,
    "chat": mc.FAMILY_CHAT,
    "chat_completion": mc.FAMILY_CHAT,
    "text_generation": mc.FAMILY_CHAT,
    "causal_lm": mc.FAMILY_CHAT,
    "image_text_to_text": mc.FAMILY_CHAT,
    "image_question_answering": mc.FAMILY_CHAT,
    "embedding": mc.FAMILY_EMBEDDING,
    "embeddings": mc.FAMILY_EMBEDDING,
    "text_embedding": mc.FAMILY_EMBEDDING,
    "feature_extraction": mc.FAMILY_EMBEDDING,
    "text_to_image": mc.FAMILY_IMAGE,
    "image_to_image": mc.FAMILY_IMAGE,
    "text_to_video": mc.FAMILY_VIDEO,
    "automatic_speech_recognition": mc.FAMILY_AUDIO,
    "text_to_speech": mc.FAMILY_AUDIO,
    "rerank": mc.FAMILY_RERANK,
    "reranking": mc.FAMILY_RERANK,
    "classification": mc.FAMILY_CLASSIFICATION,
    "text_classification": mc.FAMILY_CLASSIFICATION,
    "moderation": mc.FAMILY_MODERATION,
}

_PARAMETER_CAPABILITIES = {
    "tools": mc.CAP_TOOL_CALL,
    "tool_choice": mc.CAP_TOOL_CALL,
    "parallel_tool_calls": mc.CAP_TOOL_CALL,
    "function_calling": mc.CAP_TOOL_CALL,
    "response_format": mc.CAP_JSON_MODE,
    "structured_output": mc.CAP_STRUCTURED_OUTPUT,
    "structured_outputs": mc.CAP_STRUCTURED_OUTPUT,
    "json_schema": mc.CAP_STRUCTURED_OUTPUT,
    "reasoning": mc.CAP_REASONING,
    "reasoning_effort": mc.CAP_REASONING,
    "include_reasoning": mc.CAP_REASONING,
    "web_search": mc.CAP_WEB_SEARCH,
    "web_search_options": mc.CAP_WEB_SEARCH,
}


def _shape_token(value: Any) -> str:
    return compact_str(value).lower().replace("-", "_").replace(" ", "_")


def _family_from_explicit_fields(raw: Mapping[str, Any]) -> str:
    for key in ("type", "model_type", "task", "pipeline_tag"):
        family = _TYPE_FAMILIES.get(_shape_token(raw.get(key)))
        if family:
            return family
    return mc.FAMILY_UNKNOWN


def _modalities(raw: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    architecture = as_mapping(raw.get("architecture"))
    input_modalities = modalities_from_value(
        raw.get("input_modalities") or architecture.get("input_modalities")
    )
    output_modalities = modalities_from_value(
        raw.get("output_modalities") or architecture.get("output_modalities")
    )
    if not input_modalities or not output_modalities:
        arrow_input, arrow_output = split_modality_arrow(
            raw.get("modality") or architecture.get("modality")
        )
        input_modalities = input_modalities or arrow_input
        output_modalities = output_modalities or arrow_output
    return input_modalities, output_modalities


def _capabilities_from_modalities(
    input_modalities: tuple[str, ...],
    output_modalities: tuple[str, ...],
) -> tuple[str, ...]:
    input_set = set(input_modalities)
    output_set = set(output_modalities)
    out: list[str] = []
    if mc.MODALITY_IMAGE in input_set and mc.MODALITY_TEXT in output_set:
        out.append(mc.CAP_VISION)
    if mc.MODALITY_FILE in input_set:
        out.append(mc.CAP_FILES)
    if mc.MODALITY_PDF in input_set:
        out.append(mc.CAP_PDF)
    if mc.MODALITY_AUDIO in input_set:
        out.append(mc.CAP_AUDIO_INPUT)
    if mc.MODALITY_AUDIO in output_set:
        out.append(mc.CAP_AUDIO_OUTPUT)
    if mc.MODALITY_IMAGE in output_set:
        out.append(mc.CAP_IMAGE_GENERATION)
        if mc.MODALITY_IMAGE in input_set:
            out.append(mc.CAP_IMAGE_EDITING)
    if mc.MODALITY_VIDEO in output_set:
        out.append(mc.CAP_VIDEO_GENERATION)
    return tuple(out)


def _explicit_capabilities(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[Any] = []
    payload = raw.get("capabilities")
    if isinstance(payload, Mapping):
        supports = payload.get("supports")
        if isinstance(supports, Mapping):
            values.extend(key for key, enabled in supports.items() if enabled is True)
        values.extend(key for key, enabled in payload.items() if enabled is True)
    elif isinstance(payload, (list, tuple)):
        values.extend(payload)

    out: list[str] = []
    for value in values:
        cap = mc.normalize_capability(value)
        if cap and cap not in out:
            out.append(cap)
    for value in as_list(raw.get("supported_parameters")):
        cap = _PARAMETER_CAPABILITIES.get(_shape_token(value))
        if cap and cap not in out:
            out.append(cap)
    task = next(
        (_shape_token(raw.get(key)) for key in ("type", "model_type", "task", "pipeline_tag") if raw.get(key)),
        "",
    )
    task_capability = {
        "automatic_speech_recognition": mc.CAP_TRANSCRIPTION,
        "text_to_speech": mc.CAP_TTS,
        "text_to_image": mc.CAP_IMAGE_GENERATION,
        "image_to_image": mc.CAP_IMAGE_EDITING,
        "text_to_video": mc.CAP_VIDEO_GENERATION,
        "image_text_to_text": mc.CAP_VISION,
        "image_question_answering": mc.CAP_VISION,
    }.get(task)
    if task_capability and task_capability not in out:
        out.append(task_capability)
    return tuple(out)


def _limits(raw: Mapping[str, Any]) -> dict[str, int]:
    architecture = as_mapping(raw.get("architecture"))
    top_provider = as_mapping(raw.get("top_provider"))
    limits: dict[str, int] = {}
    for keys, target in (
        (("context_length", "max_context_length", "max_model_len"), "context_tokens"),
        (("input_token_limit", "inputTokenLimit"), "input_tokens"),
        (("output_token_limit", "outputTokenLimit", "max_completion_tokens"), "output_tokens"),
    ):
        for key in keys:
            value = int_limit(raw.get(key)) or int_limit(architecture.get(key)) or int_limit(top_provider.get(key))
            if value:
                limits[target] = value
                break
    return limits


def _default_modalities(
    family: str,
    raw: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    task = next(
        (_shape_token(raw.get(key)) for key in ("type", "model_type", "task", "pipeline_tag") if raw.get(key)),
        "",
    )
    task_modalities = {
        "automatic_speech_recognition": ((mc.MODALITY_AUDIO,), (mc.MODALITY_TEXT,)),
        "text_to_speech": ((mc.MODALITY_TEXT,), (mc.MODALITY_AUDIO,)),
        "text_to_image": ((mc.MODALITY_TEXT,), (mc.MODALITY_IMAGE,)),
        "image_to_image": ((mc.MODALITY_IMAGE,), (mc.MODALITY_IMAGE,)),
        "text_to_video": ((mc.MODALITY_TEXT,), (mc.MODALITY_VIDEO,)),
        "image_text_to_text": ((mc.MODALITY_TEXT, mc.MODALITY_IMAGE), (mc.MODALITY_TEXT,)),
        "image_question_answering": ((mc.MODALITY_TEXT, mc.MODALITY_IMAGE), (mc.MODALITY_TEXT,)),
    }.get(task)
    if task_modalities:
        return task_modalities
    if family == mc.FAMILY_CHAT:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,)
    if family == mc.FAMILY_EMBEDDING:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_EMBEDDING,)
    if family == mc.FAMILY_IMAGE:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_IMAGE,)
    if family == mc.FAMILY_VIDEO:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_VIDEO,)
    if family == mc.FAMILY_AUDIO:
        return (), ()
    if family in {mc.FAMILY_RERANK, mc.FAMILY_CLASSIFICATION, mc.FAMILY_MODERATION}:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,)
    return (), ()


def record_from_model(
    raw: Mapping[str, Any],
    *,
    vendor_id: str = VENDOR_GENERIC_OPENAI,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id", "name", "model")
    if not model_id:
        return None

    family = _family_from_explicit_fields(raw)
    input_modalities, output_modalities = _modalities(raw)
    if family == mc.FAMILY_UNKNOWN:
        family = family_from_modalities(input_modalities, output_modalities)
    if family != mc.FAMILY_UNKNOWN and not input_modalities and not output_modalities:
        input_modalities, output_modalities = _default_modalities(family, raw)
    capabilities = merge_unique(
        _explicit_capabilities(raw),
        _capabilities_from_modalities(input_modalities, output_modalities),
    )
    limits = _limits(raw)
    if family == mc.FAMILY_UNKNOWN and not capabilities and not limits:
        capability = mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        )
    else:
        capability = build_capability(
            family=family,
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            capabilities=capabilities,
            limits=limits,
        )
    return ModelCapabilityRecord(
        vendor=vendor_id,
        model_id=model_id,
        stable_model_id=stable_model_id_for(vendor_id, model_id, endpoint_id=endpoint_id, base_url=base_url),
        display_name=compact_str(raw.get("display_name") or raw.get("name")),
        capability=capability,
        deterministic_controls=deterministic_controls_from_supported_parameters(
            raw.get("supported_parameters")
        ),
        model_family=compact_str(raw.get("root") or raw.get("model_family")),
        raw=raw,
    )


def records_from_payload(
    payload: Any,
    *,
    vendor_id: str = VENDOR_GENERIC_OPENAI,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    records: list[ModelCapabilityRecord] = []
    for item in openai_model_items(payload):
        record = record_from_model(item, vendor_id=vendor_id, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
