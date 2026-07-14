"""Shared ControlNet two-pass helper (pass1 txt2img → pass2 canny from pass1)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

from titan.control_net import resolve_control_for_scheduler

LOG = logging.getLogger("titan.control_net_two_pass")


def normalize_control_net_flag(raw: Any) -> Optional[bool]:
    """Parse bool from API/MCP/UI; None when unset."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def default_control_net_enabled() -> bool:
    """Default OFF — chat enables via user text; Image Studio via explicit toggle."""
    return False


def resolve_control_net_enabled(*, raw: Dict[str, Any], proposal: Dict[str, Any]) -> bool:
    for source in (raw, proposal):
        if "control_net" in source:
            flag = normalize_control_net_flag(source.get("control_net"))
            if flag is not None:
                return flag
    return False


def control_net_weight() -> float:
    try:
        weight = float(os.environ.get("TITAN_CONTROLNET_WEIGHT", "0.55"))
    except ValueError:
        weight = 0.55
    return max(0.1, min(0.9, weight))


def two_pass_eligible(
    *,
    op: str,
    proposal: Dict[str, Any],
    body: Dict[str, Any],
    control_net_enabled: bool,
) -> bool:
    if not control_net_enabled:
        return False
    if op != "generate":
        return False
    if (proposal.get("style") or body.get("style")) == "krea":
        return False
    if body.get("image"):
        return False
    if proposal.get("control") or body.get("control"):
        return False
    if int(body.get("n") or 1) > 1:
        return False
    return True


async def scheduler_two_pass_generations(
    body: Dict[str, Any],
    *,
    scheduler_url: str,
    owner: Optional[str] = None,
    weight: Optional[float] = None,
    timeout: Optional[httpx.Timeout] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Run txt2img then ControlNet canny. Returns (scheduler_json, used_two_pass)."""
    if timeout is None:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=20.0, pool=20.0)

    pass1_body = dict(body)
    pass1_body.pop("control", None)
    # Keep SD loaded between passes; only the final pass should shutdown_after.
    pass1_body["shutdown_after"] = False

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp1 = await client.post(f"{scheduler_url.rstrip('/')}/v1/images/generations", json=pass1_body)
        if resp1.status_code != 200:
            raise _scheduler_http_error(resp1)

        try:
            data1 = resp1.json()
        except Exception as exc:
            raise RuntimeError("Scheduler returned non-JSON on pass 1") from exc

        images1 = (data1 or {}).get("data") or []
        pass1_b64 = (images1[0] or {}).get("b64_json") if images1 else None
        if not pass1_b64:
            raise RuntimeError("No image returned from scheduler on pass 1")

        w = weight if weight is not None else control_net_weight()
        resolved_control = resolve_control_for_scheduler(
            {"type": "canny", "b64": pass1_b64, "weight": w, "preprocess": True},
            owner=owner,
        )
        if not resolved_control:
            LOG.warning("ControlNet pass 2 skipped — could not resolve control image")
            return data1, False

        pass2_body = dict(body)
        pass2_body["control"] = resolved_control
        resp2 = await client.post(f"{scheduler_url.rstrip('/')}/v1/images/generations", json=pass2_body)
        if resp2.status_code != 200:
            LOG.warning("ControlNet pass 2 failed (%s), using pass 1", resp2.status_code)
            return data1, False

        try:
            data2 = resp2.json()
        except Exception:
            LOG.warning("ControlNet pass 2 returned non-JSON, using pass 1")
            return data1, False

        images2 = (data2 or {}).get("data") or []
        if not images2 or not (images2[0] or {}).get("b64_json"):
            LOG.warning("ControlNet pass 2 returned no image, using pass 1")
            return data1, False

        return data2, True


def _scheduler_http_error(resp: httpx.Response) -> RuntimeError:
    error_text = resp.text[:500]
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            error_text = err.get("message", error_text)
        elif err:
            error_text = str(err)
    except Exception:
        pass
    return RuntimeError(f"Image generation failed ({resp.status_code}): {error_text}")
