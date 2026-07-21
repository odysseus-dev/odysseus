"""LM Studio native model metadata reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers import generic_openai
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_LMSTUDIO,
    as_list,
    as_mapping,
    build_capability,
    compact_str,
    int_limit,
    merge_unique,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_LMSTUDIO


def _loaded_instance_contexts(raw: Mapping[str, Any]) -> tuple[int, ...]:
    contexts: list[int] = []
    for instance in as_list(raw.get("loaded_instances")):
        instance_payload = as_mapping(instance)
        config = as_mapping(instance_payload.get("config"))
        value = int_limit(instance_payload.get("context_length")) or int_limit(
            config.get("context_length")
        )
        if value:
            contexts.append(value)
    return tuple(contexts)


def _limits_from_model(raw: Mapping[str, Any]) -> dict[str, Any]:
    limits: dict[str, Any] = {}
    loaded_contexts = _loaded_instance_contexts(raw)
    loaded_context = int_limit(raw.get("loaded_context_length")) or (
        min(loaded_contexts) if loaded_contexts else None
    )
    configured_context = int_limit(raw.get("context_length")) or int_limit(raw.get("contextLength"))
    max_context = int_limit(raw.get("max_context_length")) or int_limit(raw.get("maxContextLength"))
    context_tokens = loaded_context or configured_context or max_context
    if context_tokens:
        limits["context_tokens"] = context_tokens
    if max_context and max_context != context_tokens:
        limits["max_context_tokens"] = max_context
    return limits


def _family_from_type(raw: Mapping[str, Any]) -> str:
    kind = compact_str(raw.get("type") or raw.get("model_type") or raw.get("task")).lower().replace("-", "_")
    if kind in {"embedding", "embeddings", "text_embedding", "text_embeddings"}:
        return mc.FAMILY_EMBEDDING
    if kind in {"llm", "chat", "vlm", "vision", "text_generation"}:
        return mc.FAMILY_CHAT
    return mc.FAMILY_UNKNOWN


def _capabilities_from_native_payload(raw: Mapping[str, Any]) -> tuple[str, ...]:
    capabilities_payload = as_mapping(raw.get("capabilities"))
    capabilities: list[str] = []
    if capabilities_payload.get("vision") is True:
        capabilities.append(mc.CAP_VISION)
    if (
        capabilities_payload.get("trained_for_tool_use") is True
        or capabilities_payload.get("tools") is True
        or capabilities_payload.get("tool_use") is True
    ):
        capabilities.append(mc.CAP_TOOL_CALL)
    if capabilities_payload.get("reasoning"):
        capabilities.append(mc.CAP_REASONING)
    return merge_unique(capabilities)


def _unknown_record(
    raw: Mapping[str, Any],
    model_id: str,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord:
    return ModelCapabilityRecord(
        vendor=VENDOR_LMSTUDIO,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_LMSTUDIO,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(raw.get("display_name") or raw.get("name")) or model_id,
        capability=mc.unknown_capability(
            source=mc.SOURCE_PROVIDER_READER,
            confidence=mc.CONFIDENCE_UNKNOWN,
        ),
        raw=raw,
    )


def record_from_native_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "key", "id", "model", "name")
    if not model_id:
        return None

    family = _family_from_type(raw)
    capabilities = _capabilities_from_native_payload(raw)

    if family == mc.FAMILY_UNKNOWN and capabilities:
        family = mc.FAMILY_CHAT

    if family == mc.FAMILY_EMBEDDING:
        input_modalities = (mc.MODALITY_TEXT,)
        output_modalities = (mc.MODALITY_EMBEDDING,)
    elif family == mc.FAMILY_CHAT and mc.CAP_VISION in capabilities:
        input_modalities = (mc.MODALITY_TEXT, mc.MODALITY_IMAGE)
        output_modalities = (mc.MODALITY_TEXT,)
    elif family == mc.FAMILY_CHAT:
        input_modalities = (mc.MODALITY_TEXT,)
        output_modalities = (mc.MODALITY_TEXT,)
    else:
        return generic_openai.record_from_model(
            raw,
            vendor_id=VENDOR_LMSTUDIO,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ) or _unknown_record(
            raw,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )

    capability = build_capability(
        family=family,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        capabilities=capabilities,
        limits=_limits_from_model(raw),
    )
    return ModelCapabilityRecord(
        vendor=VENDOR_LMSTUDIO,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_LMSTUDIO,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=compact_str(raw.get("display_name") or raw.get("name")) or model_id,
        capability=capability,
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
        record = record_from_native_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    if records:
        return tuple(records)
    for item in as_list(as_mapping(payload).get("models")):
        if not isinstance(item, Mapping):
            continue
        record = record_from_native_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
