"""ChatGPT Subscription Codex model-list identity reader."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_CHATGPT_SUBSCRIPTION,
    as_list,
    as_mapping,
    compact_str,
    identity_str,
    stable_model_id_for,
)


vendor = VENDOR_CHATGPT_SUBSCRIPTION
_DEFAULT_PRIORITY = 10_000


def _priority_rank(raw: Mapping[str, Any]) -> int | float:
    value = raw.get("priority")
    if isinstance(value, bool):
        return _DEFAULT_PRIORITY
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return _DEFAULT_PRIORITY


def _is_hidden(raw: Mapping[str, Any]) -> bool:
    visibility = raw.get("visibility")
    return (
        isinstance(visibility, str)
        and visibility.strip().lower() in {"hide", "hidden"}
    )


def select_catalog_items(
    items: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    """Apply the provider's visibility, priority, and slug de-duplication."""

    sortable: list[tuple[int | float, str, int, Mapping[str, Any]]] = []
    passthrough: list[Mapping[str, Any]] = []
    for position, item in enumerate(items):
        if _is_hidden(item):
            continue
        slug = identity_str(item.get("slug"))
        if not slug:
            passthrough.append(item)
            continue
        sortable.append((_priority_rank(item), slug, position, item))
    sortable.sort(key=lambda entry: (entry[0], entry[1], entry[2]))

    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for _, slug, _, item in sortable:
        if slug not in seen:
            selected.append(item)
            seen.add(slug)
    selected.extend(passthrough)
    return tuple(selected)


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = identity_str(raw.get("slug"))
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
        for item in select_catalog_items(
            tuple(item for item in as_list(values) if isinstance(item, Mapping))
        )
        if (record := record_from_model(item, endpoint_id=endpoint_id, base_url=base_url))
    )
