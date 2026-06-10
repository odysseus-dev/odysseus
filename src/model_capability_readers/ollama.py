"""Ollama native API capability reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_OLLAMA,
    as_list,
    as_mapping,
    build_capability,
    compact_str,
    int_limit,
    merge_unique,
    model_id_from,
    stable_model_id_for,
)


vendor = VENDOR_OLLAMA


_CAPABILITY_MAP = {
    "completion": None,
    "completions": None,
    "chat": None,
    "thinking": mc.CAP_REASONING,
    "reasoning": mc.CAP_REASONING,
    "vision": mc.CAP_VISION,
    "tools": mc.CAP_TOOL_CALL,
    "tool": mc.CAP_TOOL_CALL,
    "embedding": None,
    "embeddings": None,
}


def _capability_tokens(values: Any) -> tuple[str, ...]:
    out: list[str] = []
    for value in as_list(values):
        token = compact_str(value).lower().replace("-", "_")
        cap = _CAPABILITY_MAP.get(token)
        if cap and cap not in out:
            out.append(cap)
    return tuple(out)


def _family_from_ollama_capabilities(values: Any) -> str:
    tokens = {compact_str(value).lower().replace("-", "_") for value in as_list(values)}
    if tokens and tokens.issubset({"embedding", "embeddings"}):
        return mc.FAMILY_EMBEDDING
    if "embedding" in tokens or "embeddings" in tokens:
        return mc.FAMILY_EMBEDDING
    if tokens.intersection({"completion", "completions", "chat", "thinking", "reasoning", "tools", "tool", "vision"}):
        return mc.FAMILY_CHAT
    return mc.FAMILY_UNKNOWN


def _parameters_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    text = compact_str(value)
    if not text:
        return {}
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            parsed[parts[0]] = parts[1]
    return parsed


def _modalities_for_family(family: str, capabilities: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if family == mc.FAMILY_EMBEDDING:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_EMBEDDING,)
    if family == mc.FAMILY_CHAT and mc.CAP_VISION in capabilities:
        return (mc.MODALITY_TEXT, mc.MODALITY_IMAGE), (mc.MODALITY_TEXT,)
    if family == mc.FAMILY_CHAT:
        return (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,)
    return (), ()


def _first_int_by_key_shape(*mappings: Mapping[str, Any], exact_keys: tuple[str, ...] = ()) -> int | None:
    for key in exact_keys:
        for mapping in mappings:
            value = int_limit(mapping.get(key))
            if value:
                return value
    for mapping in mappings:
        for key, value in mapping.items():
            key_text = compact_str(key).lower()
            if key_text == "context_length" or key_text.endswith(".context_length"):
                limit = int_limit(value)
                if limit:
                    return limit
    return None


def _limits_from_show(raw: Mapping[str, Any]) -> dict[str, Any]:
    model_info = as_mapping(raw.get("model_info"))
    parameters = _parameters_mapping(raw.get("parameters"))
    details = as_mapping(raw.get("details"))
    limits: dict[str, Any] = {}
    context_tokens = _first_int_by_key_shape(
        raw,
        model_info,
        parameters,
        details,
        exact_keys=("context_length", "num_ctx"),
    )
    if context_tokens:
        limits["context_tokens"] = context_tokens
    return limits


def record_from_show_payload(
    model_id: str,
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = compact_str(model_id) or model_id_from(payload, "model", "name")
    if not model_id:
        return None
    capability_values = payload.get("capabilities")
    capabilities = _capability_tokens(capability_values)
    family = _family_from_ollama_capabilities(capability_values)
    if family == mc.FAMILY_UNKNOWN:
        capability = mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        )
    else:
        input_modalities, output_modalities = _modalities_for_family(family, capabilities)
        capability = build_capability(
            family=family,
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            capabilities=merge_unique(capabilities),
            limits=_limits_from_show(payload),
        )
    return ModelCapabilityRecord(
        vendor=VENDOR_OLLAMA,
        model_id=model_id,
        stable_model_id=stable_model_id_for(VENDOR_OLLAMA, model_id, endpoint_id=endpoint_id, base_url=base_url),
        display_name=model_id,
        capability=capability,
        raw=payload,
    )


def records_from_tags_payload(
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    records: list[ModelCapabilityRecord] = []
    for item in as_list(as_mapping(payload).get("models")):
        if not isinstance(item, Mapping):
            continue
        model_id = model_id_from(item, "model", "name")
        if not model_id:
            continue
        records.append(
            ModelCapabilityRecord(
                vendor=VENDOR_OLLAMA,
                model_id=model_id,
                stable_model_id=stable_model_id_for(
                    VENDOR_OLLAMA,
                    model_id,
                    endpoint_id=endpoint_id,
                    base_url=base_url,
                ),
                display_name=model_id,
                capability=mc.unknown_capability(
                    source=mc.SOURCE_PROVIDER_READER,
                    confidence=mc.CONFIDENCE_UNKNOWN,
                ),
                raw=item,
            )
        )
    return tuple(records)


def records_from_payload(
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    payload = as_mapping(payload)
    if "models" in payload:
        return records_from_tags_payload(payload, endpoint_id=endpoint_id, base_url=base_url)
    record = record_from_show_payload(
        model_id_from(payload, "model", "name"),
        payload,
        endpoint_id=endpoint_id,
        base_url=base_url,
    )
    return (record,) if record else ()
