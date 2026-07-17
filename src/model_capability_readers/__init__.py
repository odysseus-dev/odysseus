"""Vendor-specific model capability reader registry."""

from __future__ import annotations

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
    ModelCapabilityRecord,
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
    if reader is generic_openai:
        record_vendor = vendor_id if vendor_id else VENDOR_UNKNOWN
        records = reader.records_from_payload(
            payload,
            vendor_id=record_vendor,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
    else:
        records = reader.records_from_payload(payload, endpoint_id=endpoint_id, base_url=base_url)
    return tuple(
        replace(
            record,
            provider_source=resolution.provider_source,
            catalog_shape_id=resolution.shape_id,
            fallback=resolution.fallback,
        )
        for record in records
    )


__all__ = [
    "ModelCapabilityRecord",
    "CANONICAL_MODEL_SHAPE_VERSION",
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
