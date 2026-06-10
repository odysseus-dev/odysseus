"""llama.cpp server capability reader.

llama-server exposes OpenAI-compatible model IDs through /v1/models, but its
useful runtime metadata lives in native endpoints such as /props and /slots.
This reader can normalize each payload independently and can merge the three
payloads when the probe script has them all.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers import generic_openai
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_LLAMACPP,
    as_list,
    as_mapping,
    build_capability,
    compact_str,
    deterministic_controls_from_supported_parameters,
    int_limit,
    merge_unique,
    model_id_from,
    openai_model_items,
    stable_model_id_for,
)


vendor = VENDOR_LLAMACPP


_SAMPLER_CONTROL_MAP = {
    "temperature": mc.CONTROL_TEMPERATURE,
    "top_p": mc.CONTROL_TOP_P,
}


def _model_entries(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    payload = as_mapping(payload)
    data_items = openai_model_items(payload)
    if data_items:
        return data_items
    return tuple(item for item in as_list(payload.get("models")) if isinstance(item, Mapping))


def _server_model_entries(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(item for item in as_list(as_mapping(payload).get("models")) if isinstance(item, Mapping))


def _model_id_from_props(payload: Mapping[str, Any]) -> str:
    payload = as_mapping(payload)
    model_alias = compact_str(payload.get("model_alias"))
    if model_alias:
        return model_alias
    model_path = compact_str(payload.get("model_path"))
    if model_path:
        return PurePosixPath(model_path).name
    return ""


def _capability_tokens_from_server_model(raw: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for value in as_list(raw.get("capabilities")):
        token = compact_str(value).lower().replace("-", "_")
        if token in {"embedding", "embeddings"}:
            continue
        if token in {"rerank", "reranking"}:
            continue
        if token in {"completion", "completions", "chat"}:
            continue
        cap = mc.normalize_capability(token)
        if cap and cap not in out:
            out.append(cap)
    return tuple(out)


def _family_from_server_model(raw: Mapping[str, Any]) -> str:
    capabilities = {compact_str(value).lower().replace("-", "_") for value in as_list(raw.get("capabilities"))}
    if "embedding" in capabilities or "embeddings" in capabilities:
        return mc.FAMILY_EMBEDDING
    if "rerank" in capabilities or "reranking" in capabilities:
        return mc.FAMILY_RERANK
    if "completion" in capabilities or "completions" in capabilities or "chat" in capabilities:
        return mc.FAMILY_CHAT
    return mc.FAMILY_UNKNOWN


def _matching_server_model(payload: Mapping[str, Any], model_id: str) -> Mapping[str, Any]:
    for item in _server_model_entries(payload):
        if model_id in {
            model_id_from(item, "id", "name", "model"),
            compact_str(item.get("name")),
            compact_str(item.get("model")),
        }:
            return item
    return {}


def _limits_from_model_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
    meta = as_mapping(raw.get("meta"))
    limits: dict[str, Any] = {}
    n_ctx_train = int_limit(raw.get("n_ctx_train") or meta.get("n_ctx_train"))
    n_params = int_limit(raw.get("n_params") or meta.get("n_params"))
    size = int_limit(raw.get("size") or meta.get("size"))
    if n_ctx_train:
        limits["training_context_tokens"] = n_ctx_train
    if n_params:
        limits["parameters"] = n_params
    if size:
        limits["model_bytes"] = size
    return limits


def _props_params(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return as_mapping(as_mapping(payload.get("default_generation_settings")).get("params"))


def _limits_from_props(payload: Mapping[str, Any], slots_payload: Any = None) -> dict[str, Any]:
    default_settings = as_mapping(payload.get("default_generation_settings"))
    limits: dict[str, Any] = {}
    n_ctx = int_limit(default_settings.get("n_ctx"))
    total_slots = int_limit(payload.get("total_slots"))
    if not n_ctx and isinstance(slots_payload, list):
        slot_contexts = [int_limit(as_mapping(slot).get("n_ctx")) for slot in slots_payload]
        slot_contexts = [value for value in slot_contexts if value]
        if slot_contexts:
            n_ctx = min(slot_contexts)
    if n_ctx:
        limits["context_tokens"] = n_ctx
    if total_slots:
        limits["parallel_slots"] = total_slots
    elif isinstance(slots_payload, list) and slots_payload:
        limits["parallel_slots"] = len(slots_payload)
    return limits


def _modalities_from_props(payload: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    modalities = as_mapping(payload.get("modalities"))
    input_modalities = [mc.MODALITY_TEXT]
    output_modalities = [mc.MODALITY_TEXT]
    if modalities.get("vision") is True:
        input_modalities.append(mc.MODALITY_IMAGE)
    if modalities.get("audio") is True:
        input_modalities.append(mc.MODALITY_AUDIO)
    return tuple(input_modalities), tuple(output_modalities)


def _capabilities_from_props(payload: Mapping[str, Any]) -> tuple[str, ...]:
    caps = as_mapping(payload.get("chat_template_caps"))
    params = _props_params(payload)
    out: list[str] = []
    if caps.get("supports_tools") is True or caps.get("supports_tool_calls") is True:
        out.append(mc.CAP_TOOL_CALL)
    if params.get("stream") is not None:
        out.append(mc.CAP_STREAMING)
    if as_mapping(payload.get("modalities")).get("vision") is True:
        out.append(mc.CAP_VISION)
    if as_mapping(payload.get("modalities")).get("audio") is True:
        out.append(mc.CAP_AUDIO_INPUT)
    return tuple(out)


def _unsupported_assertions_from_props(payload: Mapping[str, Any]) -> tuple[mc.CapabilityAssertion, ...]:
    modalities = as_mapping(payload.get("modalities"))
    assertions: list[mc.CapabilityAssertion] = []
    if modalities.get("vision") is False:
        assertions.append(
            mc.CapabilityAssertion.build(
                capability=mc.CAP_VISION,
                status=mc.ASSERTION_UNSUPPORTED,
                source=mc.SOURCE_PROVIDER_READER,
                confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
                evidence={"field": "modalities.vision"},
            )
        )
    if modalities.get("audio") is False:
        assertions.append(
            mc.CapabilityAssertion.build(
                capability=mc.CAP_AUDIO_INPUT,
                status=mc.ASSERTION_UNSUPPORTED,
                source=mc.SOURCE_PROVIDER_READER,
                confidence=mc.CONFIDENCE_PROVIDER_REPORTED,
                evidence={"field": "modalities.audio"},
            )
        )
    return tuple(assertions)


def _deterministic_controls_from_props(payload: Mapping[str, Any]) -> tuple[mc.DeterministicControl, ...]:
    controls: list[str] = []
    params = _props_params(payload)
    for key in ("temperature", "top_p", "seed"):
        if key in params:
            controls.append(key)
    for sampler in as_list(params.get("samplers")):
        control = _SAMPLER_CONTROL_MAP.get(compact_str(sampler).lower())
        if control:
            controls.append(control)
    template_caps = as_mapping(payload.get("chat_template_caps"))
    if template_caps.get("supports_system_role") is True:
        controls.append(mc.CONTROL_SYSTEM_PROMPT)
    if template_caps.get("supports_tools") is True or template_caps.get("supports_tool_calls") is True:
        controls.append(mc.CONTROL_TOOL_CHOICE)
    return deterministic_controls_from_supported_parameters(merge_unique(controls))


def _capability_for_family(
    family: str,
    *,
    capabilities: tuple[str, ...] = (),
    limits: Mapping[str, Any] | None = None,
    props_payload: Mapping[str, Any] | None = None,
) -> mc.ModelCapability:
    if family == mc.FAMILY_EMBEDDING:
        return build_capability(
            family=mc.FAMILY_EMBEDDING,
            input_modalities=(mc.MODALITY_TEXT,),
            output_modalities=(mc.MODALITY_EMBEDDING,),
            capabilities=capabilities,
            limits=limits,
        )
    if family == mc.FAMILY_RERANK:
        return build_capability(
            family=mc.FAMILY_RERANK,
            input_modalities=(mc.MODALITY_TEXT,),
            output_modalities=(mc.MODALITY_TEXT,),
            capabilities=capabilities,
            limits=limits,
        )
    if props_payload:
        input_modalities, output_modalities = _modalities_from_props(props_payload)
    else:
        input_modalities, output_modalities = (mc.MODALITY_TEXT,), (mc.MODALITY_TEXT,)
    return build_capability(
        family=mc.FAMILY_CHAT,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        capabilities=capabilities,
        limits=limits,
    )


def _record(
    *,
    model_id: str,
    family: str,
    capabilities: tuple[str, ...] = (),
    limits: Mapping[str, Any] | None = None,
    props_payload: Mapping[str, Any] | None = None,
    deterministic_controls: tuple[mc.DeterministicControl, ...] = (),
    extra_assertions: tuple[mc.CapabilityAssertion, ...] = (),
    raw: Mapping[str, Any] | None = None,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord:
    capability = _capability_for_family(
        family,
        capabilities=capabilities,
        limits=limits,
        props_payload=props_payload,
    )
    return ModelCapabilityRecord(
        vendor=VENDOR_LLAMACPP,
        model_id=model_id,
        stable_model_id=stable_model_id_for(VENDOR_LLAMACPP, model_id, endpoint_id=endpoint_id, base_url=base_url),
        display_name=model_id,
        capability=capability,
        capability_assertions=(
            mc.capability_assertions_from_capability(
                capability,
                status=mc.ASSERTION_CLAIMED,
                source=capability.source,
                confidence=capability.confidence,
            )
            + extra_assertions
        ),
        deterministic_controls=deterministic_controls,
        raw=raw or {},
    )


def record_from_model_payload(
    raw: Mapping[str, Any],
    *,
    server_model: Mapping[str, Any] | None = None,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    model_id = model_id_from(raw, "id", "name", "model")
    if not model_id:
        return None
    server_model = as_mapping(server_model)
    family = _family_from_server_model(server_model) if server_model else mc.FAMILY_UNKNOWN
    if family == mc.FAMILY_UNKNOWN:
        return generic_openai.record_from_model(
            raw,
            vendor_id=VENDOR_LLAMACPP,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
    capabilities = _capability_tokens_from_server_model(server_model)
    return _record(
        model_id=model_id,
        family=family,
        capabilities=capabilities,
        limits=_limits_from_model_entry(raw),
        raw=raw,
        endpoint_id=endpoint_id,
        base_url=base_url,
    )


def record_from_props_payload(
    payload: Mapping[str, Any],
    *,
    slots_payload: Any = None,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> ModelCapabilityRecord | None:
    payload = as_mapping(payload)
    model_id = _model_id_from_props(payload)
    if not model_id:
        return None
    return _record(
        model_id=model_id,
        family=mc.FAMILY_CHAT,
        capabilities=_capabilities_from_props(payload),
        limits=_limits_from_props(payload, slots_payload),
        props_payload=payload,
        deterministic_controls=_deterministic_controls_from_props(payload),
        extra_assertions=_unsupported_assertions_from_props(payload),
        raw=payload,
        endpoint_id=endpoint_id,
        base_url=base_url,
    )


def records_from_payloads(
    *,
    models_payload: Mapping[str, Any] | None = None,
    props_payload: Mapping[str, Any] | None = None,
    slots_payload: Any = None,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    props_payload = as_mapping(props_payload)
    models_payload = as_mapping(models_payload)
    props_record = (
        record_from_props_payload(props_payload, slots_payload=slots_payload, endpoint_id=endpoint_id, base_url=base_url)
        if props_payload
        else None
    )
    if not models_payload:
        return (props_record,) if props_record else ()

    records: list[ModelCapabilityRecord] = []
    for item in _model_entries(models_payload):
        model_id = model_id_from(item, "id", "name", "model")
        if not model_id:
            continue
        server_model = _matching_server_model(models_payload, model_id)
        model_record = record_from_model_payload(
            item,
            server_model=server_model,
            endpoint_id=endpoint_id,
            base_url=base_url,
        )
        if not model_record:
            continue
        if props_record and props_record.model_id == model_id:
            limits = {**dict(model_record.capability.limits), **dict(props_record.capability.limits)}
            capability = _capability_for_family(
                props_record.capability.family,
                capabilities=merge_unique(model_record.capability.capabilities, props_record.capability.capabilities),
                limits=limits,
                props_payload=props_payload,
            )
            records.append(
                ModelCapabilityRecord(
                    vendor=VENDOR_LLAMACPP,
                    model_id=model_id,
                    stable_model_id=stable_model_id_for(
                        VENDOR_LLAMACPP,
                        model_id,
                        endpoint_id=endpoint_id,
                        base_url=base_url,
                    ),
                    display_name=model_id,
                    capability=capability,
                    capability_assertions=(
                        mc.capability_assertions_from_capability(
                            capability,
                            status=mc.ASSERTION_CLAIMED,
                            source=capability.source,
                            confidence=capability.confidence,
                        )
                        + _unsupported_assertions_from_props(props_payload)
                    ),
                    deterministic_controls=props_record.deterministic_controls,
                    raw={"models": item, "props": props_payload, "slots": slots_payload or []},
                )
            )
        else:
            records.append(model_record)
    if not records and props_record:
        records.append(props_record)
    return tuple(records)


def records_from_payload(
    payload: Mapping[str, Any],
    *,
    endpoint_id: Any = "",
    base_url: Any = "",
) -> tuple[ModelCapabilityRecord, ...]:
    payload = as_mapping(payload)
    if not payload:
        return ()
    if "default_generation_settings" in payload or "chat_template_caps" in payload:
        record = record_from_props_payload(payload, endpoint_id=endpoint_id, base_url=base_url)
        return (record,) if record else ()
    if "models" in payload or "data" in payload:
        return records_from_payloads(models_payload=payload, endpoint_id=endpoint_id, base_url=base_url)
    return ()
