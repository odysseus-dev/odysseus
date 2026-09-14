"""GitHub Copilot model-catalog capability reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_COPILOT,
    as_mapping,
    build_capability,
    compact_str,
    int_limit,
    merge_unique,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_COPILOT

_SUPPORT_CAPABILITIES = {
    "tool_calls": mc.CAP_TOOL_CALL,
    "vision": mc.CAP_VISION,
}


def select_catalog_items(
    items: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    """Keep picker-enabled models when the catalog advertises any of them."""

    if any(item.get("model_picker_enabled") is True for item in items):
        return tuple(
            item for item in items if item.get("model_picker_enabled") is True
        )
    return items


def _supports(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    return as_mapping(as_mapping(raw.get("capabilities")).get("supports"))


def _limits(raw: Mapping[str, Any]) -> dict[str, int]:
    payload = as_mapping(raw.get("limits"))
    out: dict[str, int] = {}
    for keys, target in (
        (("max_prompt_tokens", "input_tokens"), "input_tokens"),
        (("max_output_tokens", "output_tokens"), "output_tokens"),
        (("max_context_tokens", "context_window"), "context_tokens"),
    ):
        for key in keys:
            value = int_limit(payload.get(key)) or int_limit(raw.get(key))
            if value:
                out[target] = value
                break
    return out


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id")
    if not model_id:
        return None
    supports = _supports(raw)
    capabilities = merge_unique(
        _SUPPORT_CAPABILITIES[key]
        for key, enabled in supports.items()
        if enabled is True and key in _SUPPORT_CAPABILITIES
    )
    picker_enabled = raw.get("model_picker_enabled") is True
    if picker_enabled or capabilities:
        inputs = [mc.MODALITY_TEXT]
        if mc.CAP_VISION in capabilities:
            inputs.append(mc.MODALITY_IMAGE)
        capability = build_capability(
            family=mc.FAMILY_CHAT,
            input_modalities=inputs,
            output_modalities=(mc.MODALITY_TEXT,),
            capabilities=capabilities,
            limits=_limits(raw),
        )
    else:
        capability = mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        )
    return ModelCapabilityRecord(
        vendor=VENDOR_COPILOT,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_COPILOT,
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
    for item in select_catalog_items(openai_model_items(payload)):
        record = record_from_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
