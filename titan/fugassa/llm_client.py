"""Fugassa wizard LLM — uses Titan default chat model (general)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import HTTPException

from src.endpoint_resolver import resolve_chat_fallback_candidates, resolve_endpoint_by_id
from src.llm_core import llm_call_async_with_fallback
from src.settings import get_user_setting, load_settings

log = logging.getLogger("titan.fugassa.llm")

_SCHEDULER_URL = os.environ.get("TITAN_SCHEDULER_URL", "http://host.docker.internal:8150").rstrip("/")
_USE_SCHEDULER = os.environ.get("TITAN_LLM_VIA_SCHEDULER", "true").lower() not in ("0", "false", "no")
# Matches the scheduler's own `llm_ready_timeout_sec` (see titan-scheduler/config.yaml,
# currently 180s for this 35B GGUF) plus slack for the HTTP round-trip itself.
_ENSURE_LLM_TIMEOUT_SEC = float(os.environ.get("TITAN_ENSURE_LLM_TIMEOUT_SEC", "200"))


class FugassaLlmDisabled(Exception):
    pass


async def _ensure_llm_awake() -> None:
    """
    Ask the Titan VRAM scheduler to (re)start the local LLM and swap out SD if
    needed, blocking until it reports ready.

    The wizard/GM chat talks to the model server directly (see
    `_build_candidates` below), but the VRAM scheduler is free to stop that
    server entirely whenever an image job needs the VRAM (see
    `titan-scheduler/scheduler.py::allocate_sd`). Without this pre-flight
    call, any Fugassa LLM request made while the scheduler is holding SD
    (or the LLM is simply idle/stopped) fails instantly with a connection
    error — this is what made the wizard chat and the in-game opening scene
    look "unresponsive" even though nothing was actually broken.
    Best-effort only: if the scheduler is unreachable (e.g. dev environment
    without it), we swallow the error and let the direct LLM call proceed
    (and fail with its own, clearer error if the model truly isn't up).
    """
    if not _USE_SCHEDULER:
        return
    try:
        async with httpx.AsyncClient(timeout=_ENSURE_LLM_TIMEOUT_SEC) as client:
            resp = await client.post(f"{_SCHEDULER_URL}/v1/external/ensure-llm", json={})
            if resp.status_code >= 400:
                log.warning("VRAM scheduler ensure-llm returned %s: %s", resp.status_code, resp.text[:300])
    except Exception as exc:  # noqa: BLE001 — scheduler is optional infra, never block on it
        log.warning("VRAM scheduler ensure-llm unreachable, proceeding without it: %s", exc)


def _build_candidates(owner: str | None) -> list[tuple[str, str, dict | None]]:
    settings = load_settings()
    ep_id = (get_user_setting("default_endpoint_id", owner or "", settings.get("default_endpoint_id", "")) or "").strip()
    model = (get_user_setting("default_model", owner or "", settings.get("default_model", "")) or "").strip()
    primary = resolve_endpoint_by_id(ep_id, model, owner=owner) if ep_id else None
    chain: list[tuple[str, str, dict | None]] = []
    if primary:
        chain.append(primary)
    chain.extend(resolve_chat_fallback_candidates(owner=owner))
    # dedupe by url+model
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, dict | None]] = []
    for url, m, headers in chain:
        key = (url, m)
        if key in seen:
            continue
        seen.add(key)
        out.append((url, m, headers))
    return out


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    owner: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    candidates = _build_candidates(owner)
    if not candidates:
        raise HTTPException(status_code=503, detail="No LLM endpoint configured for Fugassa wizard")
    await _ensure_llm_awake()
    try:
        return await llm_call_async_with_fallback(
            candidates,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            prompt_type="fugassa_wizard",
            # The wizard's "Suggest 3" / "Regenerate" actions are explicitly meant to
            # produce a fresh completion for the same inputs — a cache hit would
            # silently return last time's identical text/prompt without ever
            # touching the LLM again, which reads as "regenerate does nothing".
            use_cache=False,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Fugassa LLM call failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}") from exc


async def wizard_chat(
    messages: list[dict[str, str]],
    *,
    owner: str | None,
    llm_enabled: bool,
    max_tokens: int = 7000,
) -> str:
    if not llm_enabled:
        raise FugassaLlmDisabled("LLM is disabled in Fugassa Settings")
    return await chat_completion(messages, owner=owner, max_tokens=max_tokens, temperature=0.7)
