"""Anthropic Models API identity reader.

The current Model resource is availability/identity metadata, not an explicit
per-model capability card, so records stay unknown.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_ANTHROPIC,
    compact_str,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_ANTHROPIC


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id")
    if not model_id:
        return None
    return ModelCapabilityRecord(
        vendor=VENDOR_ANTHROPIC,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_ANTHROPIC,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(raw.get("display_name")) or model_id,
        capability=mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        ),
        raw=raw,
    )


def records_from_payload(
    payload: Any,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    return tuple(
        record
        for item in openai_model_items(payload)
        if (record := record_from_model(item, endpoint_id=endpoint_id, base_url=base_url))
    )
