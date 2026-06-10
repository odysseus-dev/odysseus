"""Per-model reasoning control — Category #1: the ``/think`` soft-switch directive.

Introduces a per-model preference (``ModelEndpoint.reasoning_modes``, a JSON map
``{model_id: "on" | "off"}``; absent = ``"auto"`` = leave the model's default) and,
for models that gate reasoning via the Qwen3/Nemotron ``/think`` soft-switch,
injects ``/think`` into the latest user message when reasoning is turned on.

Why per-model (not per-endpoint or global): whether reasoning can be toggled —
and *how* — is a property of the model, and one endpoint can serve several models
that differ. Odysseus had no per-model settings mechanism (``supports_tools`` etc.
are per-endpoint), so this adds a minimal one keyed by model id, with the
``/think`` directive as its first consumer.

── Reasoning-toggle implementations across the ecosystem ──
Only Category #1 is implemented here; the rest are catalogued so this can grow
without reshaping. (Full map + sources: docs / reasoning-toggle taxonomy.)

  Prompt-injection (mutate the messages):
    #1 user-message soft-switch  "/think" / "/no_think"      [IMPLEMENTED]  Qwen3, Nemotron-VL, Hunyuan
    #2 system-prompt instruction "detailed thinking on/off"  [future]       Llama-Nemotron (Nano/Super/nim-nano)
  Request-body field (add a field to the outgoing request):
    #3 chat_template_kwargs.enable_thinking (bool)           [future]       Qwen3, DeepSeek-V3.1/3.2 self-host, GLM, Granite
    #4 native top-level bool (e.g. Ollama "think")           [future]       Ollama, DashScope
    #5 structured {type: enabled|disabled|adaptive} object   [future]       Anthropic, GLM/Z.ai, DeepSeek/Kimi/Cohere (hosted)
  Graded — a separate setting, not a binary toggle (out of scope):
    #6 budget where a sentinel disables (Gemini thinkingBudget: 0)
    #8 reasoning_effort (low|medium|high)                                   OpenAI o-series/GPT-5, Grok, Mistral
  (#7 "reasoning is a separate model id" is model selection, not a toggle — excluded.)

To add a category later: extend the family detection below for prompt-injection
styles (#2), or have the resolver also return a request-body fragment for the
body-field styles (#3–#5), merged into the payload in llm_core.stream_llm.

── Scope of this slice ──
This is *default-off ``/think`` enablement*: the targeted family (Nemotron-VL)
reasons only when ``/think`` is present, so ``on`` injects it and ``off``/``auto``
inject nothing. The ``/no_think`` direction (turning reasoning OFF on default-ON
``/think`` models such as Qwen3) is the natural next increment and is
intentionally not included here.

── Alignment with the #2739 capability schema ──
Two follow-ups land once #2739's control evidence is wired (and persisted) at
runtime; both swap points are isolated to a single function each:
  • dispatch off control evidence rather than the model-name heuristic — key on
    #2739's ``REASONING_CONTROL_reasoning_message_directive`` instead of
    ``_is_think_directive_model`` (see TODO there);
  • resolve the preference by stable endpoint/model identity rather than the
    URL-based lookup (see ``_endpoint_for``).
Until then this stays standalone: name heuristic + model-aware URL lookup.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

AUTO, ON, OFF = "auto", "on", "off"

# Category #1 — models that gate reasoning via the "/think" soft-switch.
# Nemotron-VL is OFF by default and responds ONLY to /think (it ignores the
# enable_thinking kwarg), so for this family `on` -> "/think" and `off`/`auto`
# inject nothing. Substring match; extend this set (and add /no_think handling
# for default-ON families like Qwen3) to cover more #1 models.
# TODO(#2739): replace this name-substring dispatch with #2739 control evidence
# (REASONING_CONTROL_reasoning_message_directive) once that evidence is wired at
# runtime — see "Alignment with the #2739 capability schema" above.
_THINK_DIRECTIVE_MODELS = ("nemotron-nano-12b-vl", "nemotron-nano-vl")


def _is_think_directive_model(model: str) -> bool:
    m = (model or "").lower()
    if any(p in m for p in _THINK_DIRECTIVE_MODELS):
        return True
    return "nemotron" in m and "-vl" in m  # any Nemotron-VL variant


def reasoning_directive(model: str, mode: str) -> Optional[str]:
    """The Category-#1 message directive to inject for this model + preference, or None.

    Only the "/think" soft-switch is implemented (default-off enablement). For
    the Nemotron-VL family (default OFF): `on` -> "/think"; `off`/`auto` -> None.
    The `/no_think` off-direction for default-ON models is not handled yet.
    Models that use a
    different mechanism (#2–#5) or have no per-request toggle return None, so the
    request is left unchanged — nothing is ever sent to a model that wouldn't
    understand it.
    """
    if mode != ON:
        return None
    return "/think" if _is_think_directive_model(model) else None


def inject_directive(messages: List[dict], directive: str) -> None:
    """Prepend `directive` to the latest user message, in place (string or
    multimodal-list content). No-op if it's already present."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if directive not in content:
                msg["content"] = f"{directive} {content}"
        elif isinstance(content, list):
            if not any(isinstance(b, dict) and directive in (b.get("text") or "") for b in content):
                msg["content"] = [{"type": "text", "text": directive}] + content
        return


def reasoning_mode_for(model: str, endpoint_url: str) -> str:
    """The stored *user preference* (`on`/`off`) for this model on the endpoint
    serving `endpoint_url`, else `auto`. Never raises.

    `auto`/`on`/`off` here is intent (what the user wants), kept distinct from
    capability metadata (what the model/provider supports). `auto` means "no
    explicit choice — leave the model's default", NOT "the provider advertises an
    adaptive mode" (that is a #2739 capability concept, resolved separately).
    """
    try:
        ep = _endpoint_for(model, endpoint_url)
        raw = getattr(ep, "reasoning_modes", None) if ep is not None else None
        if not raw:
            return AUTO
        modes = json.loads(raw) if isinstance(raw, str) else (raw or {})
        val = modes.get(model) or modes.get((model or "").lower())
        return val if val in (ON, OFF) else AUTO
    except Exception as e:
        logger.debug("reasoning_mode_for failed for %s: %s", endpoint_url, e)
        return AUTO


def _endpoint_serves(ep, model: str) -> bool:
    """Whether `model` is among an endpoint's visible model ids — cached or
    pinned, minus any that failed probing (`hidden_models`)."""
    if not model:
        return False
    visible, hidden = set(), set()
    for attr, sink in (("cached_models", visible),
                       ("pinned_models", visible),
                       ("hidden_models", hidden)):
        raw = getattr(ep, attr, None)
        if not raw:
            continue
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if isinstance(ids, list):
            sink.update(ids)
    return model in (visible - hidden)


def _endpoint_for(model: str, endpoint_url: str):
    """Resolve the ModelEndpoint whose preference applies to (model, url).

    One base URL can be shared by several endpoint rows (different api keys /
    owners / model sets), so a URL-only "first match" can read the wrong row.
    Among the URL matches we therefore prefer the row that actually serves
    `model`, falling back to the first (preserving the old behaviour when only
    one matches or none lists the model).

    This is the best identity signal available at the stream layer today, which
    sees url + model but not the endpoint id. The fuller fix — resolving by stable
    endpoint/model identity — is the #2739-aligned step once that evidence is
    threaded through (see the dispatch TODO above).

    Reuses agent_loop's candidate-key logic (lazy import to avoid an import cycle).
    """
    from core.database import SessionLocal, ModelEndpoint
    try:
        from src.agent_loop import _endpoint_lookup_keys
        keys = _endpoint_lookup_keys(endpoint_url)
    except Exception:
        raw = (endpoint_url or "").strip()
        keys = [raw, raw.rstrip("/")]
    db = SessionLocal()
    try:
        matches, seen = [], set()
        for key in keys:
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.base_url == key).all():
                if ep.id not in seen:
                    seen.add(ep.id)
                    matches.append(ep)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        for ep in matches:  # disambiguate same-base-url rows by model membership
            if _endpoint_serves(ep, model):
                return ep
        return matches[0]
    finally:
        db.close()
