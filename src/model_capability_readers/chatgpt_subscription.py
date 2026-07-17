"""ChatGPT Subscription Codex model-list identity reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_CHATGPT_SUBSCRIPTION,
    as_list,
    as_mapping,
    compact_str,
    stable_model_id_for,
)


vendor = VENDOR_CHATGPT_SUBSCRIPTION


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = compact_str(raw.get("slug"))
    if not model_id:
        return None
    return ModelCapabilityRecord(
        vendor=VENDOR_CHATGPT_SUBSCRIPTION,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_CHATGPT_SUBSCRIPTION,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(raw.get("display_name") or raw.get("title")) or model_id,
        capability=mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        ),
        model_family=compact_str(raw.get("family")),
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
