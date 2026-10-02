# services/laya/service.py
"""laya decision-engine service — Odysseus-facing interface.

This is the only part of Odysseus other modules should import. It wraps the HTTP
client with the integration's **fail-open** policy: when laya is disabled or
unreachable, methods here return a benign result and never raise, so a missing or
broken sidecar can never take Odysseus down. Callers that want to act on a laya
decision check ``decision.ok and decision.acted``.

Capabilities (all read-only, advisory): ``route`` / ``guard`` / ``moderate`` /
``triage``. Every call runs the same loop: build questions → call laya (bounded
transient retry) → deterministically validate → interpret → audit → return a
typed ``LayaDecision``. In M2 the only wired consumer is ``route`` in **shadow
mode** (computed + audited + logged, never acted on).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from . import audit
from . import decisions as D
from . import questions as Q
from .client import LayaClient, LayaUnavailable, LayaInvalidResponse
from .config import LayaConfig
from .decisions import LayaDecision
from .validate import normalize_answers, routing_model

logger = logging.getLogger(__name__)


@dataclass
class LayaHealth:
    """Result of a health probe. Always safe to render; never implies an error
    state just because laya is intentionally disabled."""

    enabled: bool
    reachable: bool
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reachable": self.reachable,
            "detail": self.detail,
            "error": self.error,
        }


@dataclass
class GuardOutcome:
    """Result of the guard gate on the chat path. ``blocked`` is True only in
    'block' mode on a flagged input; in 'warn' mode it is always False."""

    mode: str                       # off | warn | block
    ran: bool = False               # did laya actually classify the input
    flagged: bool = False           # did laya flag it as an attack
    blocked: bool = False           # should the request be rejected
    confidence: Optional[float] = None
    user_message: Optional[str] = None   # message to show the user when blocked


# Shown to the user when a request is blocked. Never silent, per policy.
GUARD_BLOCK_MESSAGE = (
    "This message was blocked by the safety filter (it looks like a prompt-"
    "injection or unsafe-content attempt). If this is a mistake, rephrase and "
    "try again."
)


def _state_text(state: Any) -> str:
    """Extract a representative text string from a state for redaction/audit."""
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        for key in ("message", "body", "prompt", "text", "content", "post", "request"):
            val = state.get(key)
            if isinstance(val, str) and val:
                return val
        try:
            return json.dumps(state, ensure_ascii=False)[:2000]
        except Exception:
            return str(state)[:2000]
    return str(state)


class LayaService:
    """Fail-open facade over the laya sidecar."""

    def __init__(
        self,
        config: Optional[LayaConfig] = None,
        client: Optional[LayaClient] = None,
    ) -> None:
        self._config = config or LayaConfig.from_env()
        self._client = client or LayaClient(self._config)

    @property
    def enabled(self) -> bool:
        """Configured on/off (env). Independent of the runtime pause switch."""
        return self._config.enabled

    @property
    def active(self) -> bool:
        """Will decisions actually run now: enabled AND not paused by an admin."""
        return self._config.enabled and not is_paused()

    @property
    def config(self) -> LayaConfig:
        return self._config

    # ------------------------------ health ------------------------------

    async def health(self) -> LayaHealth:
        """Probe the sidecar. Fail-open: returns a result, never raises."""
        if not self._config.enabled:
            return LayaHealth(enabled=False, reachable=False)
        try:
            detail = await self._client.health()
            return LayaHealth(enabled=True, reachable=True, detail=detail or {})
        except LayaUnavailable as exc:
            logger.warning("laya health probe failed: %s", exc)
            return LayaHealth(enabled=True, reachable=False, error=str(exc))
        except Exception as exc:  # defensive: never let a probe break a page
            logger.exception("laya health probe unexpected error")
            return LayaHealth(enabled=True, reachable=False, error=str(exc))

    # --------------------------- decision loop ---------------------------

    async def _decide(
        self,
        capability: str,
        state: Any,
        questions: Dict[str, Any],
        interpret: Callable[[Dict[str, Dict[str, Any]]], Any],
        *,
        owner: Optional[str] = None,
        shadow: bool = True,
        act: bool = False,
        act_when: Optional[Callable[[LayaDecision], bool]] = None,
    ) -> LayaDecision:
        """Run one capability end-to-end. Never raises; always fail-open.

        ``acted`` records whether Odysseus acted on the decision: never in shadow
        mode, never on an invalid/absent answer; otherwise ``act_when(decision)``
        when given (e.g. guard acts only when flagged), else ``act``.
        """
        decision = LayaDecision(capability=capability, shadow=shadow)

        # Disabled or paused => no network, no run, nothing to audit.
        if not self.active:
            return decision

        t0 = time.monotonic()
        try:
            raw = await self._client.systemone(state, questions)
            decision.reachable = True
            norm = normalize_answers(raw, questions)   # deterministic validation
            primary, confidence = interpret(norm)
            decision.ok = True
            decision.primary = primary
            decision.confidence = confidence
            decision.answers = {k: v["value"] for k, v in norm.items()}
            decision.model = routing_model(raw)
            if shadow:
                decision.acted = False
            elif act_when is not None:
                decision.acted = bool(act_when(decision))
            else:
                decision.acted = bool(act)
        except LayaUnavailable as exc:
            decision.error = str(exc)
        except LayaInvalidResponse as exc:
            logger.warning("laya %s invalid response: %s", capability, exc)
            decision.error = str(exc)
        except Exception as exc:  # defensive catch-all
            logger.exception("laya %s unexpected error", capability)
            decision.error = str(exc)
        finally:
            decision.latency_ms = int((time.monotonic() - t0) * 1000)

        audit.record(decision, input_text=_state_text(state), owner=owner)
        return decision

    # --------------------------- capabilities ---------------------------

    async def route(self, state: Any, *, owner: Optional[str] = None,
                    shadow: bool = True, act: bool = False) -> LayaDecision:
        return await self._decide("route", state, Q.ROUTE_QUESTIONS,
                                  D.interpret_route, owner=owner, shadow=shadow, act=act)

    async def guard(self, state: Any, *, owner: Optional[str] = None,
                    shadow: bool = True, act: bool = False) -> LayaDecision:
        return await self._decide("guard", state, Q.GUARD_QUESTIONS,
                                  D.interpret_guard, owner=owner, shadow=shadow, act=act)

    async def moderate(self, state: Any, *, owner: Optional[str] = None,
                       shadow: bool = True, act: bool = False) -> LayaDecision:
        return await self._decide("moderate", state, Q.MODERATION_QUESTIONS,
                                  D.interpret_moderate, owner=owner, shadow=shadow, act=act)

    async def triage(self, state: Any, *, owner: Optional[str] = None,
                     shadow: bool = True, act: bool = False) -> LayaDecision:
        return await self._decide("triage", state, Q.TRIAGE_QUESTIONS,
                                  D.interpret_triage, owner=owner, shadow=shadow, act=act)

    # --------------------------- guard gate -----------------------------

    async def guard_gate(self, message: Any, *, owner: Optional[str] = None) -> GuardOutcome:
        """Run the prompt guardrail for the chat path, honoring LAYA_GUARD_MODE.

        Fail-open and non-blocking by construction: 'off' or paused/disabled runs
        nothing; any laya error leaves ``blocked=False`` so a down sidecar can
        never block a user. Only 'block' mode + a flagged input sets
        ``blocked=True``. Every run that flags is audited with ``acted=True``.
        """
        mode = self._config.guard_mode
        if mode == "off" or not self.active:
            return GuardOutcome(mode=mode, ran=False)

        decision = await self._decide(
            "guard", message, Q.GUARD_QUESTIONS, D.interpret_guard,
            owner=owner, shadow=False,
            act_when=lambda d: bool(isinstance(d.primary, dict) and d.primary.get("flagged")),
        )
        flagged = bool(decision.ok and isinstance(decision.primary, dict)
                       and decision.primary.get("flagged"))
        blocked = flagged and mode == "block"
        return GuardOutcome(
            mode=mode,
            ran=decision.ok,
            flagged=flagged,
            blocked=blocked,
            confidence=decision.confidence,
            user_message=GUARD_BLOCK_MESSAGE if blocked else None,
        )

    # ----------------------------- shadow -------------------------------

    async def shadow_route(self, message: Any, *, owner: Optional[str] = None,
                           current_model: Optional[str] = None) -> LayaDecision:
        """Observe-only routing: compute + audit + log, never act."""
        try:
            decision = await self.route(message, owner=owner, shadow=True)
        except Exception:  # route() shouldn't raise, but a shadow path must not either
            logger.exception("laya shadow_route failed (non-fatal)")
            return LayaDecision(capability="route", shadow=True)
        if decision.ok:
            logger.info(
                "laya shadow-route: tier=%s conf=%.2f (current model=%s) — observe-only",
                decision.primary, decision.confidence or 0.0, current_model or "?",
            )
        elif decision.error:
            logger.debug("laya shadow-route unavailable: %s", decision.error)
        return decision

    async def aclose(self) -> None:
        await self._client.aclose()


# ---- module singleton (routes use this; tests construct their own) ----
_service: Optional[LayaService] = None
_bg_tasks: set = set()

# Runtime pause switch, process-global and admin-controlled. Separate from the
# LAYA_ENABLED env flag: an operator can pause laya without a redeploy. Resets to
# un-paused on restart (the durable off switch is LAYA_ENABLED=false).
_paused: bool = False


def is_paused() -> bool:
    return _paused


def set_paused(value: bool) -> bool:
    global _paused
    _paused = bool(value)
    logger.info("laya %s by admin", "paused" if _paused else "resumed")
    return _paused


def get_laya_service() -> LayaService:
    """Process-wide LayaService, created on first use from the environment."""
    global _service
    if _service is None:
        _service = LayaService()
    return _service


def fire_shadow_route(message: Any, *, owner: Optional[str] = None,
                      current_model: Optional[str] = None) -> None:
    """Fire-and-forget shadow routing. Adds zero latency to the caller and can
    never raise into it. No-op when laya is disabled or there's no event loop."""
    service = get_laya_service()
    if not service.active:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # called outside an event loop; skip silently
    task = loop.create_task(
        service.shadow_route(message, owner=owner, current_model=current_model)
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
