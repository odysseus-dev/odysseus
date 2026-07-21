"""OpenAI Models API capability reader.

OpenAI's `/v1/models` list/retrieve shape currently provides model identity
metadata only: `id`, `object`, `created`, and `owned_by`. Those fields prove
availability, not model capabilities, so this reader keeps capabilities
unknown unless OpenAI adds explicit capability fields to the API shape later.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_OPENAI,
    compact_str,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_OPENAI


OFFICIAL_MODEL_FIELDS = frozenset({"id", "object", "created", "owned_by"})


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
        vendor=VENDOR_OPENAI,
        model_id=model_id,
        stable_model_id=stable_model_id_for(VENDOR_OPENAI, model_id, endpoint_id=endpoint_id, base_url=base_url),
        display_name=compact_str(raw.get("name") or raw.get("display_name")),
        capability=mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        ),
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
