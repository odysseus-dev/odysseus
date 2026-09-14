"""Vendor-specific model capability reader registry."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from src import provider_capability_schemas as pcs
from src.model_capability_readers import (
    anthropic,
    chatgpt_subscription,
    cohere,
    copilot,
    generic_openai,
    google,
    huggingface,
    llamacpp,
    lmstudio,
    mistral,
    ollama,
    openai,
    openrouter,
    sglang,
)
from src.model_capability_readers.base import (
    CANONICAL_MODEL_SHAPE_VERSION,
    CANONICAL_RUNTIME_CONTEXT_SHAPE_VERSION,
    ModelCapabilityRecord,
    RuntimeContextAllocationRecord,
    VENDOR_ANTHROPIC,
    VENDOR_CEREBRAS,
    VENDOR_CHATGPT_SUBSCRIPTION,
    VENDOR_COHERE,
    VENDOR_COPILOT,
    VENDOR_DEEPSEEK,
    VENDOR_FIREWORKS,
    VENDOR_GENERIC_OPENAI,
    VENDOR_GOOGLE,
    VENDOR_GROQ,
    VENDOR_HUGGINGFACE,
    VENDOR_LLAMACPP,
    VENDOR_LMSTUDIO,
    VENDOR_MINIMAX,
    VENDOR_MISTRAL,
    VENDOR_MOONSHOT,
    VENDOR_NVIDIA,
    VENDOR_OLLAMA,
    VENDOR_OPENAI,
    VENDOR_OPENROUTER,
    VENDOR_SGLANG,
    VENDOR_TOGETHER,
    VENDOR_UNKNOWN,
    VENDOR_VLLM,
    VENDOR_XAI,
    VENDOR_ZAI,
    detect_vendor,
    stable_model_id_for,
)


logger = logging.getLogger(__name__)


READER_MODULES = {
    VENDOR_GENERIC_OPENAI: generic_openai,
    VENDOR_OPENAI: openai,
    VENDOR_OPENROUTER: openrouter,
    VENDOR_GOOGLE: google,
    VENDOR_ANTHROPIC: anthropic,
    VENDOR_LLAMACPP: llamacpp,
    VENDOR_OLLAMA: ollama,
    VENDOR_LMSTUDIO: lmstudio,
    VENDOR_MISTRAL: mistral,
    VENDOR_COPILOT: copilot,
    VENDOR_CHATGPT_SUBSCRIPTION: chatgpt_subscription,
    VENDOR_COHERE: cohere,
    VENDOR_SGLANG: sglang,
    VENDOR_HUGGINGFACE: huggingface,
}


PLACEHOLDER_VENDOR_IDS = frozenset(
    {
        VENDOR_VLLM,
    }
)


def reader_for_vendor(vendor: Any):
    vendor_id = pcs.normalize_provider_id(vendor)
    return READER_MODULES.get(vendor_id, generic_openai)


def records_from_payload(
    payload: Any,
    *,
    vendor: str | None = None,
    base_url: str = "",
    endpoint_kind: str = "",
    endpoint_id: str = "",
) -> tuple[ModelCapabilityRecord, ...]:
    resolution = pcs.resolve_provider(
        payload,
        provider=vendor,
        base_url=base_url,
        endpoint_kind=endpoint_kind,
    )
    vendor_id = resolution.provider_id
    if vendor_id == pcs.PROVIDER_UNKNOWN:
        vendor_id = detect_vendor(base_url, endpoint_kind)
    reader = reader_for_vendor(vendor_id)

    record_vendor = vendor_id if vendor_id else VENDOR_UNKNOWN

    def annotate(
        records: tuple[ModelCapabilityRecord, ...],
        *,
        shape_id: str,
        fallback: bool,
    ) -> tuple[ModelCapabilityRecord, ...]:
        return tuple(
            replace(
                record,
                provider_source=resolution.provider_source,
                catalog_shape_id=shape_id,
                fallback=fallback,
            )
            for record in records
        )

    shape = pcs.catalog_shape_for_id(resolution.shape_id)
    if shape is None:
        records = generic_openai.records_from_payload(
            payload,
            vendor_id=record_vendor,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
        normalized = annotate(
            records,
            shape_id=resolution.shape_id,
            fallback=resolution.fallback,
        )
    elif resolution.fallback:
        normalized_records: list[ModelCapabilityRecord] = []
        for item in shape.items(payload):
            fallback_record = generic_openai.record_from_model(
                item,
                vendor_id=record_vendor,
                endpoint_id=endpoint_id,
                base_url=base_url,
            )
            if fallback_record:
                normalized_records.extend(
                    annotate(
                        (fallback_record,),
                        shape_id=shape.shape_id,
                        fallback=True,
                    )
                )
        normalized = tuple(normalized_records)
    else:
        normalized_records: list[ModelCapabilityRecord] = []
        catalog_items = shape.items(payload)
        select_catalog_items = getattr(reader, "select_catalog_items", None)
        if callable(select_catalog_items):
            catalog_items = tuple(select_catalog_items(catalog_items))
        for item in catalog_items:
            item_payload = shape.payload_for_item(payload, item)
            if shape.item_matches(item):
                if reader is generic_openai:
                    native_records = reader.records_from_payload(
                        item_payload,
                        vendor_id=record_vendor,
                        endpoint_id=endpoint_id,
                        base_url=base_url,
                    )
                else:
                    native_records = reader.records_from_payload(
                        item_payload,
                        endpoint_id=endpoint_id,
                        base_url=base_url,
                    )
                if native_records:
                    normalized_records.extend(
                        annotate(native_records, shape_id=shape.shape_id, fallback=False)
                    )
                    continue

            fallback_record = generic_openai.record_from_model(
                item,
                vendor_id=record_vendor,
                endpoint_id=endpoint_id,
                base_url=base_url,
            )
            if fallback_record:
                fallback_shape = pcs.fallback_shape_for_payload(item_payload)
                normalized_records.extend(
                    annotate(
                        (fallback_record,),
                        shape_id=fallback_shape.shape_id if fallback_shape else "",
                        fallback=True,
                    )
                )
        normalized = tuple(normalized_records)

    if logger.isEnabledFor(logging.DEBUG):
        families = sorted({record.capability.family for record in normalized})
        features = sorted(
            {
                feature
                for record in normalized
                for feature in record.capability.capabilities
            }
        )
        controls = sorted(
            {
                control.control
                for record in normalized
                for control in record.deterministic_controls
                if control.control
            }
        )
        fallback_count = sum(record.fallback for record in normalized)
        diagnostic_provider = (
            resolution.provider_id
            if resolution.provider_id in pcs.PROVIDER_SCHEMAS
            else "unknown"
            if resolution.provider_id == pcs.PROVIDER_UNKNOWN
            else "unregistered"
        )
        logger.debug(
            "[model-capability] normalized: canonical_version=%s provider=%s "
            "provider_source=%s catalog_shape=%s fallback=%s records=%d "
            "native_records=%d fallback_records=%d "
            "families=%s features=%s controls=%s",
            CANONICAL_MODEL_SHAPE_VERSION,
            diagnostic_provider,
            resolution.provider_source,
            resolution.shape_id or "unknown",
            bool(fallback_count),
            len(normalized),
            len(normalized) - fallback_count,
            fallback_count,
            families,
            features,
            controls,
        )
    return normalized


__all__ = [
    "ModelCapabilityRecord",
    "RuntimeContextAllocationRecord",
    "CANONICAL_MODEL_SHAPE_VERSION",
    "CANONICAL_RUNTIME_CONTEXT_SHAPE_VERSION",
    "PLACEHOLDER_VENDOR_IDS",
    "READER_MODULES",
    "VENDOR_ANTHROPIC",
    "VENDOR_CEREBRAS",
    "VENDOR_CHATGPT_SUBSCRIPTION",
    "VENDOR_COHERE",
    "VENDOR_COPILOT",
    "VENDOR_DEEPSEEK",
    "VENDOR_FIREWORKS",
    "VENDOR_GENERIC_OPENAI",
    "VENDOR_GOOGLE",
    "VENDOR_GROQ",
    "VENDOR_HUGGINGFACE",
    "VENDOR_LLAMACPP",
    "VENDOR_LMSTUDIO",
    "VENDOR_MINIMAX",
    "VENDOR_MISTRAL",
    "VENDOR_MOONSHOT",
    "VENDOR_NVIDIA",
    "VENDOR_OLLAMA",
    "VENDOR_OPENAI",
    "VENDOR_OPENROUTER",
    "VENDOR_SGLANG",
    "VENDOR_TOGETHER",
    "VENDOR_UNKNOWN",
    "VENDOR_VLLM",
    "VENDOR_XAI",
    "VENDOR_ZAI",
    "detect_vendor",
    "reader_for_vendor",
    "records_from_payload",
    "stable_model_id_for",
]
