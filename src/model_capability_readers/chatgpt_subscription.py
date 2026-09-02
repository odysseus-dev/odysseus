"""ChatGPT Subscription Codex model catalog capability reader.

The native catalog reports supported reasoning levels and verbosity support
per model.  These explicit fields are canonical provider evidence; model IDs,
display names, and priorities are identity/presentation data only.
"""

from __future__ import annotations

import math
import re
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
_VERBOSITY_VALUES = ("low", "medium", "high")


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
    return isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}


def select_catalog_items(items: tuple[Mapping[str, Any], ...]) -> tuple[Mapping[str, Any], ...]:
    """Apply provider visibility, priority, and exact-slug de-duplication."""
    sortable: list[tuple[int | float, str, int, Mapping[str, Any]]] = []
    for position, item in enumerate(items):
        if _is_hidden(item):
            continue
        slug = identity_str(item.get("slug"))
        if slug:
            sortable.append((_priority_rank(item), slug, position, item))
    sortable.sort(key=lambda entry: (entry[0], entry[1], entry[2]))

    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for _, slug, _, item in sortable:
        if slug not in seen:
            selected.append(item)
            seen.add(slug)
    return tuple(selected)


def _reasoning_values(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for item in as_list(raw.get("supported_reasoning_levels")):
        value = item.get("effort") if isinstance(item, Mapping) else item
        token = compact_str(value).lower().replace("-", "_")
        if token == "x_high":
            token = "xhigh"
        if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", token) and token not in values:
            values.append(token)
    return tuple(values)


def _control(control: str, allowed_values: tuple[str, ...], default: Any = "") -> mc.DeterministicControl:
    evidence: dict[str, Any] = {"allowed_values": list(allowed_values)}
    normalized_default = compact_str(default).lower().replace("-", "_")
    if normalized_default == "x_high":
        normalized_default = "xhigh"
    if normalized_default in allowed_values:
        evidence["default"] = normalized_default
    return mc.DeterministicControl.build(
        control=control,
        status=mc.ASSERTION_CLAIMED,
        source=mc.SOURCE_PROVIDER_READER,
        confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
        evidence=evidence,
    )


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = identity_str(raw.get("slug"))
    if not model_id:
        return None

    controls: list[mc.DeterministicControl] = []
    reasoning_values = _reasoning_values(raw)
    if reasoning_values:
        controls.append(_control(mc.CONTROL_REASONING_EFFORT, reasoning_values, raw.get("default_reasoning_level")))
    if raw.get("support_verbosity") is True:
        controls.append(_control(mc.CONTROL_VERBOSITY, _VERBOSITY_VALUES, raw.get("default_verbosity")))

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
        deterministic_controls=tuple(controls),
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
        for item in select_catalog_items(tuple(item for item in as_list(values) if isinstance(item, Mapping)))
        if (record := record_from_model(item, endpoint_id=endpoint_id, base_url=base_url))
    )
