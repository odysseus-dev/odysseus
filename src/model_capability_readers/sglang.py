"""SGLang `/model_info` and OpenAI model-card reader."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers import generic_openai
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_SGLANG,
    as_mapping,
    build_capability,
    deterministic_controls_from_supported_parameters,
    identity_str,
    int_limit,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_SGLANG


def _model_id(payload: Mapping[str, Any]) -> str:
    value = identity_str(payload.get("served_model_name")) or identity_str(
        payload.get("model_path")
    )
    if not value:
        return ""
    return PurePosixPath(value).name if value.startswith("/") else value


def record_from_model_info(
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = _model_id(payload)
    if not model_id:
        return None
    capabilities: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    family = mc.FAMILY_UNKNOWN
    if payload.get("is_generation") is True:
        family = mc.FAMILY_CHAT
        inputs.append(mc.MODALITY_TEXT)
        outputs.append(mc.MODALITY_TEXT)
        if payload.get("has_image_understanding") is True:
            inputs.append(mc.MODALITY_IMAGE)
            capabilities.append(mc.CAP_VISION)
        if payload.get("has_audio_understanding") is True:
            inputs.append(mc.MODALITY_AUDIO)
            capabilities.append(mc.CAP_AUDIO_INPUT)
    capability = build_capability(
        family=family,
        input_modalities=inputs,
        output_modalities=outputs,
        capabilities=capabilities,
    )
    sampling = as_mapping(payload.get("preferred_sampling_params"))
    return ModelCapabilityRecord(
        vendor=VENDOR_SGLANG,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_SGLANG,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=model_id,
        capability=capability,
        deterministic_controls=deterministic_controls_from_supported_parameters(sampling.keys()),
        raw=payload,
    )


def records_from_payload(
    payload: Any,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    mapping = as_mapping(payload)
    if "is_generation" in mapping and "model_path" in mapping:
        record = record_from_model_info(mapping, endpoint_id=endpoint_id, base_url=base_url)
        return (record,) if record else ()
    records: list[ModelCapabilityRecord] = []
    for item in openai_model_items(payload):
        record = generic_openai.record_from_model(
            item,
            vendor_id=VENDOR_SGLANG,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
        if record:
            context_tokens = int_limit(item.get("max_model_len"))
            if context_tokens:
                record = replace(
                    record,
                    capability=build_capability(
                        family=mc.FAMILY_UNKNOWN,
                        limits={"context_tokens": context_tokens},
                    ),
                )
            records.append(record)
    return tuple(records)
