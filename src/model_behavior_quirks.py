"""Canonical, exact-match model/provider behavior observations.

The registry captures behavior that cannot safely be promoted to a provider-
wide capability.  Selectors accept already-structured identity (provider,
model ID/family/version, API dialect, and canonical capabilities); they never
extract those facts from a display name with regexes or substring matching.

This is shape/evidence data only.  Runtime request builders can consume it in a
later integration pass after their endpoint has supplied structured identity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src import model_capabilities as mc
from src import provider_capability_schemas as pcs


def _identity(value: Any) -> str:
    return str(value or "").strip().lower()


def _version(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[int] = []
    for part in value:
        try:
            out.append(int(part))
        except (TypeError, ValueError):
            return ()
    return tuple(out)


@dataclass(frozen=True)
class ModelBehaviorSelector:
    providers: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()
    model_families: tuple[str, ...] = ()
    minimum_model_version: tuple[int, ...] = ()
    minimum_provider_version: tuple[int, ...] = ()
    api_dialects: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def matches(
        self,
        *,
        provider: Any,
        model_id: Any = "",
        model_family: Any = "",
        model_version: Any = (),
        provider_version: Any = (),
        api_dialect: Any = "",
        capabilities: Any = (),
    ) -> bool:
        provider_id = pcs.normalize_provider_id(provider)
        if self.providers and provider_id not in self.providers:
            return False

        identity_constraints = bool(self.model_ids or self.model_families)
        identity_match = (
            _identity(model_id) in self.model_ids
            or _identity(model_family) in self.model_families
        )
        if identity_constraints and not identity_match:
            return False

        actual_model_version = _version(model_version)
        if self.minimum_model_version and (
            not actual_model_version or actual_model_version < self.minimum_model_version
        ):
            return False
        actual_provider_version = _version(provider_version)
        if self.minimum_provider_version and (
            not actual_provider_version or actual_provider_version < self.minimum_provider_version
        ):
            return False
        if self.api_dialects and str(api_dialect or "").strip() not in self.api_dialects:
            return False

        if isinstance(capabilities, Mapping):
            capability_values: Iterable[Any] = (
                key for key, enabled in capabilities.items() if enabled is True
            )
        elif isinstance(capabilities, str):
            capability_values = (capabilities,)
        elif isinstance(capabilities, Iterable):
            capability_values = capabilities
        else:
            capability_values = ()
        normalized_caps = {
            normalized
            for value in capability_values
            if (normalized := mc.normalize_capability(value))
        }
        return set(self.required_capabilities).issubset(normalized_caps)


@dataclass(frozen=True)
class ModelBehaviorQuirk:
    quirk_id: str
    selector: ModelBehaviorSelector
    request_omit_paths: tuple[str, ...] = ()
    request_fixed_values: tuple[tuple[str, Any], ...] = ()
    required_history_paths: tuple[str, ...] = ()
    response_reasoning_paths: tuple[str, ...] = ()
    reasoning_controls: tuple[mc.ReasoningControl, ...] = ()
    status: str = mc.ASSERTION_CLAIMED
    source: str = mc.SOURCE_PROVIDER_DOCS_REGISTRY
    confidence: str = mc.CONFIDENCE_REGISTRY
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "quirk_id": self.quirk_id,
            "selector": {
                "providers": list(self.selector.providers),
                "model_ids": list(self.selector.model_ids),
                "model_families": list(self.selector.model_families),
                "minimum_model_version": list(self.selector.minimum_model_version),
                "minimum_provider_version": list(self.selector.minimum_provider_version),
                "api_dialects": list(self.selector.api_dialects),
                "required_capabilities": list(self.selector.required_capabilities),
            },
            "request_omit_paths": list(self.request_omit_paths),
            "request_fixed_values": dict(self.request_fixed_values),
            "required_history_paths": list(self.required_history_paths),
            "response_reasoning_paths": list(self.response_reasoning_paths),
            "reasoning_controls": [control.to_dict() for control in self.reasoning_controls],
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
        }


MODEL_BEHAVIOR_QUIRKS = (
    ModelBehaviorQuirk(
        quirk_id="moonshot.kimi-k2.5-k2.6.provider-fixed-temperature",
        selector=ModelBehaviorSelector(
            providers=("moonshot",),
            model_ids=("kimi-k2.5", "kimi-k2.6"),
            model_families=("kimi-k2.5", "kimi-k2.6"),
            api_dialects=(pcs.DIALECT_OPENAI_CHAT,),
        ),
        request_omit_paths=("temperature",),
        evidence_refs=(
            "github:odysseus-dev/odysseus#3960",
            "commit:f5d3e509",
        ),
    ),
    ModelBehaviorQuirk(
        quirk_id="moonshot.kimi-k2.5-k2.6.tool-history-reasoning-content",
        selector=ModelBehaviorSelector(
            providers=("moonshot",),
            model_ids=("kimi-k2.5", "kimi-k2.6"),
            model_families=("kimi-k2.5", "kimi-k2.6"),
            api_dialects=(pcs.DIALECT_OPENAI_CHAT,),
        ),
        required_history_paths=("messages[assistant+tool_calls].reasoning_content",),
        response_reasoning_paths=(
            "choices[].message.reasoning_content",
            "choices[].delta.reasoning_content",
        ),
        evidence_refs=(
            "github:odysseus-dev/odysseus#3118",
            "commit:2e6fff22",
        ),
    ),
    ModelBehaviorQuirk(
        quirk_id="anthropic.claude-opus-4.7-plus.omit-sampling-controls",
        selector=ModelBehaviorSelector(
            providers=("anthropic",),
            model_families=("claude-opus",),
            minimum_model_version=(4, 7),
            api_dialects=(pcs.DIALECT_ANTHROPIC_MESSAGES,),
        ),
        request_omit_paths=("temperature", "top_p", "top_k"),
        evidence_refs=(
            "github:odysseus-dev/odysseus#3117",
            "commit:4f48cfa9",
        ),
    ),
    ModelBehaviorQuirk(
        quirk_id="mistral.reasoning.structured-content",
        selector=ModelBehaviorSelector(
            providers=("mistral",),
            model_families=("magistral", "mistral-small", "mistral-medium"),
            api_dialects=(pcs.DIALECT_OPENAI_CHAT,),
            required_capabilities=(mc.CAP_REASONING,),
        ),
        response_reasoning_paths=(
            "choices[].message.content[type=thinking].thinking[].text",
            "choices[].delta.content[type=thinking].thinking[].text",
        ),
        reasoning_controls=(
            mc.ReasoningControl.build(
                mechanism=mc.REASONING_CONTROL_EFFORT,
                values=(mc.REASONING_CONTROL_VALUE_ON, mc.REASONING_CONTROL_VALUE_OFF),
                native_values=("high", "medium", "low", "none"),
                request_path="reasoning_effort",
                response_paths=("choices[].message.content[type=thinking]",),
                status=mc.ASSERTION_CLAIMED,
                source=mc.SOURCE_PROVIDER_DOCS_REGISTRY,
                confidence=mc.CONFIDENCE_REGISTRY,
            ),
        ),
        evidence_refs=(
            "github:odysseus-dev/odysseus#4698",
            "commit:bd9149f7",
            "https://docs.mistral.ai/capabilities/reasoning/",
        ),
    ),
    ModelBehaviorQuirk(
        quirk_id="ollama.native.reasoning-control",
        selector=ModelBehaviorSelector(
            providers=("ollama",),
            model_families=("qwen3", "deepseek-v3.1", "deepseek-r1"),
            api_dialects=(pcs.DIALECT_OLLAMA_NATIVE,),
            required_capabilities=(mc.CAP_REASONING,),
        ),
        response_reasoning_paths=("message.thinking", "thinking"),
        reasoning_controls=(
            mc.ReasoningControl.build(
                mechanism=mc.REASONING_CONTROL_NATIVE_BOOL,
                values=(mc.REASONING_CONTROL_VALUE_ON, mc.REASONING_CONTROL_VALUE_OFF),
                native_values=(True, False),
                request_path="think",
                response_paths=("message.thinking", "thinking"),
                status=mc.ASSERTION_CLAIMED,
                source=mc.SOURCE_PROVIDER_DOCS_REGISTRY,
                confidence=mc.CONFIDENCE_REGISTRY,
            ),
        ),
        evidence_refs=(
            "https://docs.ollama.com/capabilities/thinking",
            "github:odysseus-dev/odysseus#3031",
        ),
    ),
    ModelBehaviorQuirk(
        quirk_id="ollama.native.gpt-oss-reasoning-level",
        selector=ModelBehaviorSelector(
            providers=("ollama",),
            model_families=("gpt-oss", "gptoss"),
            api_dialects=(pcs.DIALECT_OLLAMA_NATIVE,),
            required_capabilities=(mc.CAP_REASONING,),
        ),
        response_reasoning_paths=("message.thinking", "thinking"),
        reasoning_controls=(
            mc.ReasoningControl.build(
                mechanism=mc.REASONING_CONTROL_EFFORT,
                values=(mc.REASONING_CONTROL_VALUE_ON,),
                native_values=("low", "medium", "high"),
                request_path="think",
                response_paths=("message.thinking", "thinking"),
                status=mc.ASSERTION_CLAIMED,
                source=mc.SOURCE_PROVIDER_DOCS_REGISTRY,
                confidence=mc.CONFIDENCE_REGISTRY,
            ),
        ),
        evidence_refs=("https://docs.ollama.com/capabilities/thinking",),
    ),
    ModelBehaviorQuirk(
        quirk_id="ollama.openai-compat.0.20.6-reasoning-disable",
        selector=ModelBehaviorSelector(
            providers=("ollama",),
            model_families=("qwen3.5",),
            minimum_provider_version=(0, 20, 6),
            api_dialects=(pcs.DIALECT_OPENAI_CHAT,),
            required_capabilities=(mc.CAP_REASONING,),
        ),
        request_fixed_values=(("reasoning_effort", "none"),),
        status=mc.ASSERTION_CLAIMED,
        source=mc.SOURCE_HEURISTIC,
        confidence=mc.CONFIDENCE_HEURISTIC,
        evidence_refs=("github:odysseus-dev/odysseus#5503",),
    ),
)


def matching_quirks(**identity: Any) -> tuple[ModelBehaviorQuirk, ...]:
    return tuple(
        quirk
        for quirk in MODEL_BEHAVIOR_QUIRKS
        if quirk.selector.matches(**identity)
    )


__all__ = [
    "MODEL_BEHAVIOR_QUIRKS",
    "ModelBehaviorQuirk",
    "ModelBehaviorSelector",
    "matching_quirks",
]
