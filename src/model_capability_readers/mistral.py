"""Mistral native model-catalog capability reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_MISTRAL,
    as_mapping,
    build_capability,
    compact_str,
    int_limit,
    merge_unique,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_MISTRAL


def _family(raw: Mapping[str, Any]) -> str:
    capabilities = as_mapping(raw.get("capabilities"))
    if capabilities.get("classification") is True and not (
        capabilities.get("completion_chat") is True
        or capabilities.get("completion_fim") is True
    ):
        return mc.FAMILY_CLASSIFICATION
    if capabilities.get("completion_chat") is True or capabilities.get("completion_fim") is True:
        return mc.FAMILY_CHAT
    return mc.FAMILY_UNKNOWN


def _capabilities(raw: Mapping[str, Any]) -> tuple[str, ...]:
    payload = as_mapping(raw.get("capabilities"))
    values: list[str] = []
    for key, capability in (
        ("vision", mc.CAP_VISION),
        ("function_calling", mc.CAP_TOOL_CALL),
        ("reasoning", mc.CAP_REASONING),
        ("structured_output", mc.CAP_STRUCTURED_OUTPUT),
        ("structured_outputs", mc.CAP_STRUCTURED_OUTPUT),
    ):
        if payload.get(key) is True:
            values.append(capability)
    return merge_unique(values)


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id")
    if not model_id:
        return None
    family = _family(raw)
    capabilities = _capabilities(raw)
    if family == mc.FAMILY_CHAT:
        inputs = [mc.MODALITY_TEXT]
        if mc.CAP_VISION in capabilities:
            inputs.append(mc.MODALITY_IMAGE)
        input_modalities = tuple(inputs)
        output_modalities = (mc.MODALITY_TEXT,)
    elif family == mc.FAMILY_CLASSIFICATION:
        input_modalities = (mc.MODALITY_TEXT,)
        output_modalities = (mc.MODALITY_TEXT,)
    else:
        input_modalities = ()
        output_modalities = ()
    context_tokens = int_limit(raw.get("max_context_length"))
    limits = {"context_tokens": context_tokens} if context_tokens else {}
    capability = build_capability(
        family=family,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        capabilities=capabilities,
        limits=limits,
    )
    return ModelCapabilityRecord(
        vendor=VENDOR_MISTRAL,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_MISTRAL,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(raw.get("name")) or model_id,
        capability=capability,
        raw=raw,
    )


def records_from_payload(
    payload: Any,
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
