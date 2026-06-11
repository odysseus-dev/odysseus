"""OpenRouter model catalog capability reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers import generic_openai
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_OPENROUTER,
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


vendor = VENDOR_OPENROUTER


_SUPPORTED_PARAMETER_CAPS = {
    "tools": mc.CAP_TOOL_CALL,
    "tool_choice": mc.CAP_TOOL_CALL,
    "function_calling": mc.CAP_TOOL_CALL,
    "parallel_tool_calls": mc.CAP_TOOL_CALL,
    "response_format": mc.CAP_JSON_MODE,
    "structured_outputs": mc.CAP_STRUCTURED_OUTPUT,
    "structured_output": mc.CAP_STRUCTURED_OUTPUT,
    "reasoning": mc.CAP_REASONING,
    "reasoning_effort": mc.CAP_REASONING,
    "include_reasoning": mc.CAP_REASONING,
    "web_search": mc.CAP_WEB_SEARCH,
    "web_search_options": mc.CAP_WEB_SEARCH,
}


def _capabilities_from_supported_parameters(values: Any) -> tuple[str, ...]:
    iterable = values if isinstance(values, list) else ()
    out: list[str] = []
    for value in iterable:
        cap = _SUPPORTED_PARAMETER_CAPS.get(compact_str(value).lower().replace("-", "_"))
        if cap and cap not in out:
            out.append(cap)
    return tuple(out)


def _limits_from_model(raw: Mapping[str, Any]) -> dict[str, Any]:
    architecture = as_mapping(raw.get("architecture"))
    top_provider = as_mapping(raw.get("top_provider"))
    per_request_limits = as_mapping(raw.get("per_request_limits"))
    limits: dict[str, Any] = {}
    for key, canonical in (
        ("context_length", "context_tokens"),
        ("max_context_length", "context_tokens"),
        ("input_token_limit", "input_tokens"),
        ("output_token_limit", "output_tokens"),
        ("max_completion_tokens", "output_tokens"),
    ):
        value = int_limit(raw.get(key) or architecture.get(key) or top_provider.get(key))
        if value:
            limits[canonical] = value
    for key, value in per_request_limits.items():
        limit = int_limit(value)
        if limit:
            limits[f"per_request_{key}"] = limit
    return limits


def _has_supported_voices(value: Any) -> bool:
    return any(compact_str(item) for item in as_list(value))


def _capabilities_from_modalities(
    input_modalities: tuple[str, ...],
    output_modalities: tuple[str, ...],
    *,
    supported_voices: Any = None,
) -> tuple[str, ...]:
    input_set = set(input_modalities)
    output_set = set(output_modalities)
    capabilities: list[str] = []
    if mc.MODALITY_IMAGE in input_set and mc.MODALITY_TEXT in output_set:
        capabilities.append(mc.CAP_VISION)
    if mc.MODALITY_FILE in input_set:
        capabilities.append(mc.CAP_FILES)
    if mc.MODALITY_PDF in input_set:
        capabilities.append(mc.CAP_PDF)
    if mc.MODALITY_AUDIO in input_set:
        capabilities.append(mc.CAP_AUDIO_INPUT)
    if mc.MODALITY_AUDIO in output_set:
        capabilities.append(mc.CAP_AUDIO_OUTPUT)
        if _has_supported_voices(supported_voices):
            capabilities.append(mc.CAP_TTS)
    if mc.MODALITY_IMAGE in output_set:
        capabilities.append(mc.CAP_IMAGE_GENERATION)
        if mc.MODALITY_IMAGE in input_set:
            capabilities.append(mc.CAP_IMAGE_EDITING)
    if mc.MODALITY_VIDEO in output_set:
        capabilities.append(mc.CAP_VIDEO_GENERATION)
    return tuple(capabilities)


def _default_parameter_controls(raw: Mapping[str, Any]) -> tuple[str, ...]:
    defaults = as_mapping(raw.get("default_parameters"))
    return tuple(key for key, value in defaults.items() if value is not None)


def _deterministic_controls_from_model(raw: Mapping[str, Any]) -> tuple[mc.DeterministicControl, ...]:
    return deterministic_controls_from_supported_parameters(
        merge_unique(
            as_list(raw.get("supported_parameters")),
            _default_parameter_controls(raw),
        )
    )


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id", "name")
    if not model_id:
        return None

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

    capabilities = list(_capabilities_from_supported_parameters(raw.get("supported_parameters")))
    capabilities.extend(
        _capabilities_from_modalities(
            input_modalities,
            output_modalities,
            supported_voices=raw.get("supported_voices"),
        )
    )

    family = family_from_modalities(input_modalities, output_modalities)
    if family == mc.FAMILY_UNKNOWN:
        fallback = generic_openai.record_from_model(
            raw,
            vendor_id=VENDOR_OPENROUTER,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
        return fallback

    capability = build_capability(
        family=family,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        capabilities=merge_unique(capabilities),
        limits=_limits_from_model(raw),
    )
    return ModelCapabilityRecord(
        vendor=VENDOR_OPENROUTER,
        model_id=model_id,
        stable_model_id=stable_model_id_for(VENDOR_OPENROUTER, model_id, endpoint_id=endpoint_id, base_url=base_url),
        display_name=compact_str(raw.get("name")) or model_id,
        capability=capability,
        deterministic_controls=_deterministic_controls_from_model(raw),
        raw=raw,
    )


def records_from_payload(
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    records: list[ModelCapabilityRecord] = []
    for item in openai_model_items(payload):
        record = record_from_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
