"""Hugging Face Hub model-info reader using explicit pipeline metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_HUGGINGFACE,
    build_capability,
    compact_str,
    model_id_from,
    stable_model_id_for,
)


vendor = VENDOR_HUGGINGFACE


# Hugging Face publishes ``pipeline_tag`` as a provider-owned task enum.  Keep
# its interpretation here, rather than teaching the inventory fallback that a
# similarly named field has the same meaning for every provider.
_PIPELINE_SHAPES = {
    "text-generation": (mc.FAMILY_CHAT, (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,), ()),
    "image-text-to-text": (
        mc.FAMILY_CHAT,
        (mc.MODALITY_TEXT, mc.MODALITY_IMAGE),
        (mc.MODALITY_TEXT,),
        (mc.CAP_VISION,),
    ),
    "image-question-answering": (
        mc.FAMILY_CHAT,
        (mc.MODALITY_TEXT, mc.MODALITY_IMAGE),
        (mc.MODALITY_TEXT,),
        (mc.CAP_VISION,),
    ),
    "feature-extraction": (
        mc.FAMILY_EMBEDDING,
        (mc.MODALITY_TEXT,),
        (mc.MODALITY_EMBEDDING,),
        (),
    ),
    "text-to-image": (
        mc.FAMILY_IMAGE,
        (mc.MODALITY_TEXT,),
        (mc.MODALITY_IMAGE,),
        (mc.CAP_IMAGE_GENERATION,),
    ),
    "image-to-image": (
        mc.FAMILY_IMAGE,
        (mc.MODALITY_IMAGE,),
        (mc.MODALITY_IMAGE,),
        (mc.CAP_IMAGE_GENERATION, mc.CAP_IMAGE_EDITING),
    ),
    "text-to-video": (
        mc.FAMILY_VIDEO,
        (mc.MODALITY_TEXT,),
        (mc.MODALITY_VIDEO,),
        (mc.CAP_VIDEO_GENERATION,),
    ),
    "automatic-speech-recognition": (
        mc.FAMILY_AUDIO,
        (mc.MODALITY_AUDIO,),
        (mc.MODALITY_TEXT,),
        (mc.CAP_TRANSCRIPTION,),
    ),
    "text-to-speech": (
        mc.FAMILY_AUDIO,
        (mc.MODALITY_TEXT,),
        (mc.MODALITY_AUDIO,),
        (mc.CAP_TTS,),
    ),
    "text-classification": (
        mc.FAMILY_CLASSIFICATION,
        (mc.MODALITY_TEXT,),
        (mc.MODALITY_TEXT,),
        (),
    ),
}


def _capability_from_pipeline_tag(value: Any) -> mc.ModelCapability:
    shape = _PIPELINE_SHAPES.get(compact_str(value).lower())
    if not shape:
        return mc.unknown_capability(
            source=mc.SOURCE_COOKBOOK_HF,
            confidence=mc.CONFIDENCE_UNKNOWN,
        )
    family, input_modalities, output_modalities, capabilities = shape
    return build_capability(
        family=family,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        capabilities=capabilities,
        source=mc.SOURCE_COOKBOOK_HF,
        confidence=mc.CONFIDENCE_REGISTRY,
    )


def record_from_model(
    raw: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "modelId", "id")
    if not model_id:
        return None
    return ModelCapabilityRecord(
        vendor=VENDOR_HUGGINGFACE,
        model_id=model_id,
        stable_model_id=stable_model_id_for(
            VENDOR_HUGGINGFACE,
            model_id,
            endpoint_id=endpoint_id,
            base_url=base_url,
        ),
        display_name=(
            compact_str(
                raw.get("cardData", {}).get("pretty_name")
                if isinstance(raw.get("cardData"), Mapping)
                else ""
            )
            or model_id
        ),
        capability=_capability_from_pipeline_tag(raw.get("pipeline_tag")),
        raw=raw,
    )


def records_from_payload(
    payload: Any,
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    if isinstance(payload, Mapping):
        record = record_from_model(payload, endpoint_id=endpoint_id, base_url=base_url)
        return (record,) if record else ()
    if not isinstance(payload, (list, tuple)):
        return ()
    records: list[ModelCapabilityRecord] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        record = record_from_model(item, endpoint_id=endpoint_id, base_url=base_url)
        if record:
            records.append(record)
    return tuple(records)
