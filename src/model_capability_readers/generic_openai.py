"""Inventory-only reader for unrecognized model-list envelopes.

Common field names are not a cross-provider capability contract.  This reader
therefore recovers model identity and preserves the original record, but never
promotes tasks, modalities, parameters, limits, or capability booleans.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_GENERIC_OPENAI,
    compact_str,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_GENERIC_OPENAI


def record_from_model(
    raw: Mapping[str, Any],
    *,
    vendor_id: str = VENDOR_GENERIC_OPENAI,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id", "name", "model", "key", "slug")
    if not model_id:
        return None
    return ModelCapabilityRecord(
        vendor=vendor_id,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            vendor_id,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(
            raw.get("display_name")
            or raw.get("name")
            or raw.get("key")
            or raw.get("slug")
        ),
        capability=mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        ),
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
        record = record_from_model(
            item,
            vendor_id=vendor_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
        if record:
            records.append(record)
    return tuple(records)
