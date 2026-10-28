"""Editor agent/planner model resolution.

The image-editor Quick Edit planner (`/api/editor/plan`, `/api/editor/agent`)
runs on an explicitly configured local vision model when one is reachable,
with the configured chat model as a fallback. This module owns:

  * the local model base URLs / model id (settings-overridable),
  * an ordered candidate list (per-request ``agent_model`` override, then the
    local Qwen3.5-9B, then the hosted default/utility chat model),
  * a vision fallback chain: local model WITH the canvas screenshot, else a
    text-only call whose image is described by a separate VL caption model,
  * a lightweight reachability probe for the configured local bases.

Everything is best-effort: when no local server is reachable the planner
transparently falls back to the previously-used hosted chat model, so the
editor agent never regresses to "no plan".
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Local endpoints are opt-in through settings or ODYSSEUS_EDITOR_AGENT_BASES.
DEFAULT_LOCAL_BASES = []
DEFAULT_LOCAL_MODEL = "Qwen/Qwen3.5-9B"

# Vision caption model used when the editor-agent model cannot accept images.
DEFAULT_CAPTION_BASE = ""
DEFAULT_CAPTION_MODEL = "Qwen/Qwen3.5-9B"

# Served-model-id cache per base (populated on first use). The local servers 400
# with "Failed to load model" when asked for an id they do not serve (e.g. the
# client's default "Qwen3.5-9B-MLX-8bit", or a stale caption model), so we
# resolve the requested id against GET {base}/models and fall back to one that
# is actually served.
_MODELS_CACHE: Dict[str, set] = {}

_CAPTION_INSTRUCTION = (
    "Describe this image in one or two sentences for a photo editor: the main "
    "subject, the background/scene, lighting, dominant colours, and any obvious "
    "flaws (blown highlights, colour cast, clutter, blur)."
)


def _setting(key: str, default: Any = "") -> Any:
    try:
        from src.settings import load_settings

        value = load_settings().get(key)
        return value if value not in (None, "") else default
    except Exception:
        return default


def local_bases() -> List[str]:
    override = _setting("editor_agent_base_url")
    if override:
        return [str(override)]
    env = os.getenv("ODYSSEUS_EDITOR_AGENT_BASES")
    if env:
        bases = [b.strip() for b in env.split(",") if b.strip()]
        if bases:
            return bases
    return list(DEFAULT_LOCAL_BASES)


def local_model() -> str:
    return str(_setting("editor_agent_model", DEFAULT_LOCAL_MODEL))


def caption_base() -> str:
    return str(_setting("editor_caption_base_url", DEFAULT_CAPTION_BASE))


def caption_model() -> str:
    return str(_setting("editor_caption_model", DEFAULT_CAPTION_MODEL))


# Per-request / per-plan context (contextvars keep this correct under the
# async event loop without sharing state between concurrent editor turns).
_override: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "editor_agent_model_override", default=None
)
_last_model: contextvars.ContextVar[Optional[Tuple[str, str]]] = contextvars.ContextVar(
    "editor_agent_last_model", default=None
)


def set_override(value: Optional[str]) -> None:
    _override.set(value or None)


def get_override() -> Optional[str]:
    return _override.get()


def set_last_model(model: str, source: str) -> None:
    _last_model.set((model, source))


def get_last_model() -> Optional[Tuple[str, str]]:
    return _last_model.get()


def _chat_url(base: str) -> str:
    b = str(base or "").rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


def _split_override(override: str) -> Tuple[str, str]:
    """Parse ``base::model`` (or a bare model id)."""
    raw = str(override or "").strip()
    if "::" in raw:
        base, model = raw.split("::", 1)
        return base.strip(), model.strip()
    return "", raw


def _has_image(messages: List[dict]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def _first_image(messages: List[dict]) -> Optional[str]:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url")
                    if isinstance(url, str):
                        return url
    return None


def _strip_images(messages: List[dict]) -> List[dict]:
    out: List[dict] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            out.append({"role": msg.get("role", "user"), "content": text})
        else:
            out.append({"role": msg.get("role", "user"), "content": content})
    return out


def _with_caption(messages: List[dict], caption: str) -> List[dict]:
    note = f"\n\n[Canvas screenshot caption: {caption}]"
    out = [dict(m) for m in messages]
    for msg in reversed(out):
        if msg.get("role") == "user":
            msg["content"] = str(msg.get("content") or "") + note
            break
    return out


async def _post_chat(
    base: str,
    model: str,
    messages: List[dict],
    headers: Optional[dict],
    timeout: float,
    max_tokens: int = 1024,
    response_format: Optional[dict] = None,
) -> Optional[str]:
    if not base or not model:
        return None
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # Constrain the planner to a strict JSON ops schema (structured outputs /
    # grammar) so the model can only emit valid editor ops. Unknown servers
    # ignore the field; the server-side schema validation is the real enforcer.
    if isinstance(response_format, dict):
        payload["response_format"] = response_format
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_chat_url(base), json=payload, headers=headers or {})
            if resp.status_code != 200:
                logger.debug(
                    "editor agent chat %s -> %s (%s)", base, resp.status_code, resp.text[:200]
                )
                return None
            data = resp.json()
    except Exception as exc:
        logger.debug("editor agent chat %s failed: %s", base, exc)
        return None
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


async def _served_models(base: str, timeout: float = 3.0) -> set:
    """Return the set of model ids a base serves (cached; empty on failure)."""
    if not base:
        return set()
    cached = _MODELS_CACHE.get(base)
    if cached is not None:
        return cached
    ids: set = set()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(base.rstrip("/") + "/models")
            if resp.status_code == 200:
                ids = {
                    str(m.get("id"))
                    for m in (resp.json().get("data") or [])
                    if isinstance(m, dict) and m.get("id")
                }
    except Exception:
        ids = set()
    _MODELS_CACHE[base] = ids
    return ids


async def _resolve_model(base: str, requested: str, fallback: str) -> str:
    """Return a model id ``base`` actually serves.

    If the requested id is served, keep it; otherwise use ``fallback`` (when
    served) or any served id. When the served list is unknown (server down),
    keep the requested id (or ``fallback``).
    """
    requested = str(requested or "").strip()
    fallback = str(fallback or "").strip()
    ids = await _served_models(base)
    if ids:
        if requested and requested in ids:
            return requested
        if fallback in ids:
            return fallback
        return sorted(ids)[0]
    return requested or fallback


async def caption_image(data_url: str, owner: Optional[str] = None, timeout: float = 90.0) -> Optional[str]:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        return None
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _CAPTION_INSTRUCTION},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    base = caption_base()
    if not base:
        return None
    model = await _resolve_model(base, caption_model(), local_model())
    return await _post_chat(base, model, messages, None, timeout, max_tokens=200)


_DONE_RE = re.compile(r"^\s*DONE\b", re.I)
_RETRY_RE = re.compile(r"RETRY:?\s*(.+)", re.I | re.S)


def _parse_self_check(text: str) -> Dict[str, Any]:
    s = str(text or "").strip()
    if _DONE_RE.match(s):
        return {"done": True, "issues": [], "retry_hint": "", "text": s}
    m = _RETRY_RE.search(s)
    if m or re.search(r"\bretry\b", s, re.I):
        hint = (m.group(1).strip() if m else s) or s
        return {"done": False, "issues": [hint], "retry_hint": hint, "text": s}
    return {"done": True, "issues": [], "retry_hint": "", "text": s}


async def self_check(
    before: Any,
    after: Any,
    prompt: str,
    model: Optional[str] = None,
    owner: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Same-origin VL self-check: compare BEFORE vs AFTER against the intent.

    The browser cannot call the local VL server directly (it sends no CORS
    headers), so ``/api/editor/self-check`` proxies here. Returns
    ``{done, issues, retry_hint, text}``; on any failure (bad input, the model
    is unreachable, or a timeout) it degrades to ``done=True`` with
    ``reason="unavailable"`` so the client never gets stuck retrying.
    """
    if not (
        isinstance(before, str) and before.startswith("data:image/")
        and isinstance(after, str) and after.startswith("data:image/")
    ):
        return {"done": True, "issues": [], "retry_hint": "",
                "reason": "unavailable", "text": "DONE"}
    bases = local_bases() or list(DEFAULT_LOCAL_BASES)
    if not bases:
        return {"done": True, "issues": [], "retry_hint": "",
                "reason": "unavailable", "text": "DONE"}
    base = bases[0]
    mdl = await _resolve_model(base, str(model or local_model()), local_model())
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": str(prompt or "")},
            {"type": "image_url", "image_url": {"url": before}},
            {"type": "image_url", "image_url": {"url": after}},
        ],
    }]
    text = await _post_chat(base, mdl, messages, None, timeout, max_tokens=140)
    if not text:
        return {"done": True, "issues": [], "retry_hint": "",
                "reason": "unavailable", "text": "DONE"}
    out = _parse_self_check(text)
    out["model"] = mdl
    out["source"] = "local"
    return out


def _candidates(owner: Optional[str], override: Optional[str]) -> List[Dict[str, Any]]:
    cands: List[Dict[str, Any]] = []
    for base in local_bases():
        cands.append({"base": base, "model": local_model(), "headers": None, "source": "local"})
    try:
        from src.endpoint_resolver import resolve_endpoint

        for prefix in ("utility", "default"):
            try:
                url, model, headers = resolve_endpoint(prefix, owner=owner)
            except Exception:
                continue
            if url and model:
                cands.append({"base": url, "model": model, "headers": headers, "source": "hosted"})
                break
    except Exception:
        pass
    if override:
        base, model = _split_override(override)
        # A request may choose a configured endpoint, never introduce a URL.
        # Prefer an exact model match (including hosted credentials) over the
        # first local endpoint when a bare model id is supplied.
        matching = [c for c in cands if not base or str(c["base"]).rstrip("/") == base.rstrip("/")]
        selected = next((c for c in matching if c["model"] == model), None)
        if selected is None and model and matching and matching[0]["source"] == "local":
            selected = {**matching[0], "model": model}
        if selected is not None:
            cands.insert(0, {**selected, "source": "override"})
    return cands


async def plan_with_editor_agent(
    messages: List[dict],
    owner: Optional[str] = None,
    override: Optional[str] = None,
    timeout: float = 90.0,
    response_format: Optional[dict] = None,
) -> Optional[Tuple[str, str, str]]:
    """Return (raw_plan_text, model_used, source) or None.

    Tries the local model WITH the screenshot, then the same model text-only
    with a VL caption of the screenshot, then the hosted chat model.

    ``response_format`` (a JSON-schema structured-output constraint) is sent
    ONLY to the local editor-agent bases, so a hosted fallback model that does
    not support the field never rejects the request.
    """
    has_img = _has_image(messages)
    text_messages = _strip_images(messages) if has_img else messages
    image = _first_image(messages) if has_img else None
    caption: Optional[str] = None
    _local = {str(b).rstrip("/") for b in local_bases()}
    for cand in _candidates(owner, override):
        base, model = cand["base"], cand["model"]
        headers = cand.get("headers")
        is_local = cand["source"] == "local" or str(base).rstrip("/") in _local
        rf = response_format if (response_format and is_local) else None
        extra = {"response_format": rf} if rf else {}
        if has_img:
            raw = await _post_chat(base, model, messages, headers, timeout, **extra)
            if raw:
                return raw, model, cand["source"]
            if caption is None and image:
                caption = await caption_image(image, owner)
            if caption:
                raw = await _post_chat(
                    base, model, _with_caption(text_messages, caption), headers, timeout, **extra,
                )
                if raw:
                    return raw, model, cand["source"] + "+caption"
        else:
            raw = await _post_chat(
                base, model, text_messages, headers, timeout, **extra
            )
            if raw:
                return raw, model, cand["source"]
    return None


async def list_editor_agent_models(owner: Optional[str] = None) -> List[Dict[str, Any]]:
    """Models available to the editor agent, for the model picker.

    Lists the local Qwen3.5-9B (one entry per reachable base) plus the hosted
    default/utility chat model that the planner falls back to.
    """
    models: List[Dict[str, Any]] = []
    seen: set = set()
    for base in local_bases():
        model = local_model()
        key = ("local", base, model)
        if key in seen:
            continue
        seen.add(key)
        models.append({
            "id": model,
            "label": f"{model} · local",
            "source": "local",
            "vision": True,
            "base_url": base,
        })
    try:
        from src.endpoint_resolver import resolve_endpoint

        for prefix in ("utility", "default"):
            try:
                url, model, _headers = resolve_endpoint(prefix, owner=owner)
            except Exception:
                continue
            if url and model:
                key = ("hosted", model)
                if key not in seen:
                    seen.add(key)
                    models.append({
                        "id": model,
                        "label": model,
                        "source": "hosted",
                        "vision": None,
                        "base_url": url,
                    })
                break
    except Exception:
        pass
    return models


# ---------------------------------------------------------------------------
# Reachability probe (best effort, non-blocking).
# ---------------------------------------------------------------------------

_PROBE: Dict[str, bool] = {}


def probe_models(base: str, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(str(base).rstrip("/") + "/models")
            return resp.status_code == 200
    except Exception:
        return False


def _probe_worker() -> None:
    for base in local_bases():
        ok = probe_models(base)
        _PROBE[base] = ok
        logger.info("editor agent local base %s reachable=%s", base, ok)
    cap = caption_base()
    if cap:
        _PROBE[cap] = probe_models(cap)
        logger.info("editor agent caption base %s reachable=%s", cap, _PROBE[cap])


def startup_probe() -> None:
    """Kick off a background reachability probe (never blocks startup)."""
    try:
        threading.Thread(target=_probe_worker, name="editor-agent-probe", daemon=True).start()
    except Exception:
        logger.debug("editor agent probe thread failed", exc_info=True)


def probe_status() -> Dict[str, bool]:
    return dict(_PROBE)
