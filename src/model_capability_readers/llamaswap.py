"""llama-swap model-list reader.

llama-swap serves an OpenAI-compatible ``/v1/models`` whose entries carry the
proxy's own metadata under ``meta.llamaswap``. That namespace is the native
shape this reader recognizes. Only operator-declared fields inside it become
capability evidence; everything else stays inventory-only, as in
:mod:`generic_openai`.

Mapped fields:

- ``meta.llamaswap.reasoning_efforts``: the efforts the operator declared for
  the model, in display order. Emitted as a claimed ``reasoning_effort``
  control whose evidence carries the levels.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers import generic_openai
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_LLAMASWAP,
    as_mapping,
    openai_model_items,
)


vendor = VENDOR_LLAMASWAP

REASONING_EFFORTS_FIELD = "meta.llamaswap.reasoning_efforts"


def _native_meta(raw: Mapping[str, Any]) -> Mapping[str, Any] | None:
    meta = as_mapping(raw.get("meta")).get("llamaswap")
    return meta if isinstance(meta, Mapping) else None


def is_native_payload(payload: Any) -> bool:
    """True when any model entry carries llama-swap's ``meta.llamaswap`` object."""
    return any(_native_meta(item) is not None for item in openai_model_items(payload))


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    record = generic_openai.record_from_model(
        raw,
        vendor_id=VENDOR_LLAMASWAP,
        endpoint_id=endpoint_id,
        base_url=base_url,
    )
    if record is None:
        return None
    native = _native_meta(raw) or {}
    effort = mc.reasoning_effort_control(native.get("reasoning_efforts"), field=REASONING_EFFORTS_FIELD)
    if effort is None:
        return record
    return replace(record, deterministic_controls=(effort,))


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
