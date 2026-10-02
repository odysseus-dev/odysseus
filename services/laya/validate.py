# services/laya/validate.py
"""Deterministic validation of laya responses.

Untrusted output from an ML service is never acted on until it passes these
plain, rule-based checks. Anything structurally wrong, out of range, or naming an
option we didn't offer raises ``LayaInvalidResponse``; the service layer then
treats the whole call as "no decision" and Odysseus falls back to its normal
behavior. No probabilities here are trusted until shape + range are proven.
"""

from __future__ import annotations

from typing import Any, Dict

from .client import LayaInvalidResponse


def _as_float(value: Any, field: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise LayaInvalidResponse(f"laya: {field} not a number: {value!r}") from exc
    if f != f:  # NaN
        raise LayaInvalidResponse(f"laya: {field} is NaN")
    return f


def _in_unit(value: float, field: str) -> float:
    if not (0.0 <= value <= 1.0):
        raise LayaInvalidResponse(f"laya: {field} out of [0,1]: {value}")
    return value


def _confidence(answer: Dict[str, Any]) -> float:
    """Prefer the calibrated answer_confidence; fall back to entropy confidence.

    Both are optional in principle; a missing confidence becomes 0.0 (which a
    threshold policy will treat as 'not confident' — the safe default)."""
    raw = answer.get("answer_confidence")
    if raw is None:
        raw = answer.get("confidence")
    if raw is None:
        return 0.0
    return _in_unit(_as_float(raw, "confidence"), "confidence")


def normalize_answers(raw: Any, questions: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Validate ``raw`` against the ``questions`` we asked and normalize it.

    Returns ``{key: {"type", "value", "confidence"}}`` where value is:
      * choice -> the chosen option label (guaranteed one we offered)
      * score  -> a float within the rubric's index range
      * noul   -> a probability in [0,1]

    Raises LayaInvalidResponse on any structural or range problem.
    """
    if not isinstance(raw, dict):
        raise LayaInvalidResponse(f"laya: response not an object: {type(raw).__name__}")
    answers = raw.get("answers")
    if not isinstance(answers, dict):
        raise LayaInvalidResponse("laya: missing 'answers' object")

    out: Dict[str, Dict[str, Any]] = {}
    for key, spec in questions.items():
        ans = answers.get(key)
        if not isinstance(ans, dict):
            raise LayaInvalidResponse(f"laya: missing answer for '{key}'")
        qtype = spec.get("type")

        if qtype == "choice":
            choice = ans.get("choice")
            options = spec.get("criteria") or {}
            allowed = set(options.keys()) if isinstance(options, dict) else set(options)
            if not isinstance(choice, str) or choice not in allowed:
                raise LayaInvalidResponse(
                    f"laya: '{key}' choice {choice!r} not in offered options {sorted(allowed)}"
                )
            value: Any = choice

        elif qtype == "score":
            score = _as_float(ans.get("score"), f"{key}.score")
            levels = spec.get("criteria") or []
            hi = max(len(levels) - 1, 0)
            if not (0.0 <= score <= hi + 1e-6):
                raise LayaInvalidResponse(f"laya: '{key}' score {score} out of [0,{hi}]")
            value = score

        elif qtype == "noul":
            value = _in_unit(_as_float(ans.get("noul"), f"{key}.noul"), f"{key}.noul")

        else:
            raise LayaInvalidResponse(f"laya: unknown question type {qtype!r} for '{key}'")

        out[key] = {"type": qtype, "value": value, "confidence": _confidence(ans)}

    return out


def routing_model(raw: Any) -> str | None:
    """Best-effort extraction of which laya checkpoint answered (for audit)."""
    if isinstance(raw, dict):
        routing = raw.get("routing")
        if isinstance(routing, dict):
            m = routing.get("model")
            if isinstance(m, str):
                return m
    return None
