"""Thin client for the local jeff-serve decision endpoint (POST /v1/systemone).

Trained decision adapters (triage, spam, guard-email, ...) answer a situation
with calibrated probabilities over named options in a single forward pass, with
no generated text to parse. Background jobs can prefer an adapter and fall back
to the normal LLM candidate chain whenever this client returns None.

Every call fails open (returns None) and is disabled per job unless the job's
flag is set, so importing/enabling this never changes behaviour on its own.

Env:
  ODYSSEUS_JEFF_URL      base URL of jeff-serve (default http://127.0.0.1:8765)
  ODYSSEUS_JEFF_TIMEOUT  per-request timeout in seconds (default 4.0)
  ODYSSEUS_JEFF_<JOB>    per-job enable flag, e.g. ODYSSEUS_JEFF_EMAIL_URGENCY=1
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://127.0.0.1:8765"
_FALSEY = ("", "0", "false", "off", "no")


def jeff_url() -> str:
    """Base URL of the local jeff-serve (no trailing slash)."""
    return os.getenv("ODYSSEUS_JEFF_URL", _DEFAULT_URL).strip().rstrip("/")


def jeff_job_enabled(job: str) -> bool:
    """True when ODYSSEUS_JEFF_<JOB> is set to a truthy value. Default off."""
    slug = "".join(c if (c.isalnum() or c == "_") else "_" for c in (job or "")).upper()
    return os.getenv("ODYSSEUS_JEFF_" + slug, "0").strip().lower() not in _FALSEY


def jeff_timeout(default: float = 4.0) -> float:
    try:
        return float(os.getenv("ODYSSEUS_JEFF_TIMEOUT", str(default)))
    except Exception:
        return default


async def jeff_systemone(
    model: str,
    state: Dict[str, Any],
    questions: Dict[str, Any],
    *,
    timeout: Optional[float] = None,
    orders: int = 1,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Dict[str, Any]]:
    """POST a decision request and return the ``answers`` dict, or None on error.

    Each question is scored in its own forward pass, so send only questions the
    adapter was trained on. Raises are swallowed and logged (fail open).
    """
    url = jeff_url()
    if not url:
        return None
    payload: Dict[str, Any] = {"model": model, "state": state, "questions": questions}
    if orders and orders != 1:
        payload["orders"] = orders
    to = timeout if timeout is not None else jeff_timeout()
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=to) as _c:
                resp = await _c.post(url + "/v1/systemone", json=payload)
        else:
            resp = await client.post(url + "/v1/systemone", json=payload)
        resp.raise_for_status()
        answers = (resp.json() or {}).get("answers")
        return answers if isinstance(answers, dict) else None
    except Exception as exc:
        logger.warning("jeff decision (%s) failed: %s", model, exc)
        return None


async def jeff_available(timeout: float = 1.5) -> bool:
    """Best-effort health probe for the local jeff-serve."""
    url = jeff_url()
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as _c:
            resp = await _c.get(url + "/health")
            return resp.status_code == 200
    except Exception:
        return False


def score_value(answer: Optional[Dict[str, Any]]) -> Optional[float]:
    """Expected value (0..n-1) of a jeff ``score`` answer, or None."""
    if not isinstance(answer, dict):
        return None
    try:
        if answer.get("type") == "score":
            value = float(answer.get("score"))
        elif answer.get("type") == "choice":
            probs = answer.get("probabilities") or {}
            value = float(sum(i * float(p) for i, p in enumerate(probs.values())))
        else:
            return None
    except Exception:
        return None
    # A malformed adapter reply must fall back like any other failure.
    return value if math.isfinite(value) else None


def choice_of(answer: Optional[Dict[str, Any]]) -> Optional[str]:
    """The winning key of a jeff ``choice`` answer, or None."""
    if isinstance(answer, dict) and answer.get("type") == "choice":
        c = answer.get("choice")
        return str(c) if c is not None else None
    return None
