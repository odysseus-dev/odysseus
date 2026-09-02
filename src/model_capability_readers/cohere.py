"""Cohere native model-catalog capability reader.

The `/v1/models` resource reports endpoint compatibility and context size per
model. It does not prove provider-wide chat/tool support for every model, so
the reader maps only those exact model-card fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_COHERE,
    as_list,
    as_mapping,
    build_capability,
    compact_str,
    deterministic_controls_from_supported_parameters,
    identity_str,
    int_limit,
    stable_model_id_for,
)


vendor = VENDOR_COHERE

_ENDPOINT_FAMILIES = {
    "chat": mc.FAMILY_CHAT,
    "generate": mc.FAMILY_CHAT,
    "embed": mc.FAMILY_EMBEDDING,
    "rerank": mc.FAMILY_RERANK,
    "classify": mc.FAMILY_CLASSIFICATION,
}


def _family(raw: Mapping[str, Any]) -> str:
    families = {
        family
        for value in as_list(raw.get("endpoints"))
        if (family := _ENDPOINT_FAMILIES.get(compact_str(value).lower()))
    }
    return next(iter(families)) if len(families) == 1 else mc.FAMILY_UNKNOWN


def _modalities(family: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if family == mc.FAMILY_CHAT:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,)
    if family == mc.FAMILY_EMBEDDING:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_EMBEDDING,)
    if family in {mc.FAMILY_RERANK, mc.FAMILY_CLASSIFICATION}:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,)
    return (), ()


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = identity_str(raw.get("name"))
    if not model_id:
        return None
    family = _family(raw)
    inputs, outputs = _modalities(family)
    context_tokens = int_limit(raw.get("context_length"))
    limits = {"context_tokens": context_tokens} if context_tokens else {}
    sampling_defaults = as_mapping(raw.get("sampling_defaults"))
    sampling_controls = (
        "top_p" if key == "p" else "top_k" if key == "k" else key
        for key in sampling_defaults
    )
    return ModelCapabilityRecord(
        vendor=VENDOR_COHERE,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_COHERE,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=model_id,
        capability=build_capability(
            family=family,
            input_modalities=inputs,
            output_modalities=outputs,
            limits=limits,
        ),
        deterministic_controls=deterministic_controls_from_supported_parameters(
            sampling_controls
        ),
        raw=raw,
    )


def records_from_payload(
    payload: Any,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    values = as_mapping(payload).get("models")
    return tuple(
        record
        for item in as_list(values)
        if isinstance(item, Mapping)
        if (record := record_from_model(item, endpoint_id=endpoint_id, base_url=base_url))
    )
