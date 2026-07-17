"""Hugging Face Hub model-info reader using explicit pipeline metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers import generic_openai
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_HUGGINGFACE,
    as_mapping,
    compact_str,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_HUGGINGFACE


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = compact_str(raw.get("modelId") or raw.get("id"))
    if not model_id:
        return None
    structural = generic_openai.record_from_model(
        {**raw, "id": model_id},
        vendor_id=VENDOR_HUGGINGFACE,
        endpoint_id=endpoint_id,
        base_url=base_url,
    )
    if not structural:
        return None
    capability = mc.ModelCapability.build(
        family=structural.capability.family,
        primary_task=structural.capability.primary_task,
        input_modalities=structural.capability.modalities.input,
        output_modalities=structural.capability.modalities.output,
        capabilities=structural.capability.capabilities,
        limits=dict(structural.capability.limits),
        source=mc.SOURCE_COOKBOOK_HF,
        confidence=mc.CONFIDENCE_REGISTRY,
    )
    config = as_mapping(raw.get("config"))
    return ModelCapabilityRecord(
        vendor=VENDOR_HUGGINGFACE,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_HUGGINGFACE,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=(
            compact_str(
                raw.get("cardData", {}).get("pretty_name")
                if isinstance(raw.get("cardData"), Mapping)
                else ""
            )
            or model_id
        ),
        capability=capability,
        deterministic_controls=structural.deterministic_controls,
        model_family=compact_str(config.get("model_type")),
        raw=raw,
    )


def records_from_payload(
    payload: Any,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    if isinstance(payload, Mapping) and (payload.get("modelId") or payload.get("pipeline_tag")):
        record = record_from_model(payload, endpoint_id=endpoint_id, base_url=base_url)
        return (record,) if record else ()
    records: list[ModelCapabilityRecord] = []
    for item in openai_model_items(payload):
        record = record_from_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
