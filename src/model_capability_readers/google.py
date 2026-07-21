"""Google Gemini model metadata reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.model_capability_readers import google_ai_studio_mapping as ai_studio
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_GOOGLE,
    as_list,
    compact_str,
    stable_model_id_for,
)


vendor = VENDOR_GOOGLE


def _model_items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    models = payload.get("models") if isinstance(payload, Mapping) else None
    if models is None and isinstance(payload, Mapping) and payload.get("name"):
        models = [payload]
    return tuple(item for item in as_list(models) if isinstance(item, Mapping))


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = ai_studio.google_model_id(raw)
    if not model_id:
        return None

    return ModelCapabilityRecord(
        vendor=VENDOR_GOOGLE,
        model_id=model_id,
        stable_model_id=stable_model_id_for(VENDOR_GOOGLE, model_id, endpoint_id=endpoint_id, base_url=base_url),
        display_name=compact_str(raw.get("displayName")) or model_id,
        capability=ai_studio.capability_from_model(raw),
        deterministic_controls=ai_studio.deterministic_controls_from_model(raw),
        raw=raw,
    )


def records_from_payload(
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    records: list[ModelCapabilityRecord] = []
    for item in _model_items(payload):
        record = record_from_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
