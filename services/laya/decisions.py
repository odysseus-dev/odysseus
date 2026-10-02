# services/laya/decisions.py
"""Typed decision results + per-capability interpretation of normalized answers.

A ``LayaDecision`` is the single return type for every capability. It is always
safe to inspect: when laya is disabled, unreachable, or returned something
invalid, ``ok`` is False and ``primary``/``confidence`` are None, so a caller that
only acts when ``decision.ok and decision.acted`` can never be driven by a bad or
absent response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from . import questions as Q
from . import policy as P


@dataclass
class LayaDecision:
    capability: str
    ok: bool = False                       # did we get a valid, usable answer
    reachable: bool = False                # was laya reachable at all
    primary: Optional[Any] = None          # capability's headline value (tier/flag/level/intent)
    confidence: Optional[float] = None     # confidence of the primary answer
    acted: bool = False                    # did Odysseus act on this (False in shadow)
    shadow: bool = True
    answers: Dict[str, Any] = field(default_factory=dict)  # all normalized answers
    model: Optional[str] = None            # laya checkpoint that answered
    latency_ms: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "ok": self.ok,
            "reachable": self.reachable,
            "primary": self.primary,
            "confidence": self.confidence,
            "acted": self.acted,
            "shadow": self.shadow,
            "answers": self.answers,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


# ---- interpreters: normalized answers -> (primary, confidence) ----
# Each returns the capability's headline value plus the confidence to audit/gate.

def interpret_route(norm: Dict[str, Dict[str, Any]]):
    a = norm["tier"]
    return a["value"], a["confidence"]


def interpret_guard(norm: Dict[str, Dict[str, Any]]):
    p = norm["attack"]["value"]            # P(attack)
    flagged = p >= P.guard_threshold()
    return {"flagged": flagged, "p_attack": p}, p


def interpret_moderate(norm: Dict[str, Dict[str, Any]]):
    score = norm["harm"]["value"]          # 0..len(levels)-1
    hi = max(len(Q.MODERATION_LEVELS) - 1, 1)
    norm_score = score / hi                 # -> [0,1]
    flagged = norm_score >= P.moderation_threshold()
    return {"flagged": flagged, "score": norm_score}, norm["harm"]["confidence"]


def interpret_triage(norm: Dict[str, Dict[str, Any]]):
    intent = norm["intent"]["value"]
    urgency = norm["urgency"]["value"]
    return {"intent": intent, "urgency": urgency}, norm["intent"]["confidence"]
