"""Desktop action — Clicky-mediated pointer and audio actions.

Backs the `desktop_act` agent tool. Pointer actions execute through the
Clicky worker's /pointer endpoint (host-side user32 injection); targets are
either raw coordinates or a `target_text` resolved from Screenpipe OCR
geometry (unique match required — never click a fuzzy match silently).

Every action is consent-gated per chat session and appended to the
operator_audit log, including denials.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request

from services.operator.core import (
    CAP_DESKTOP_ACTION,
    clicky_worker_url,
    degraded_envelope,
    envelope,
    record_audit,
    require_capability,
    screenpipe_url,
)

logger = logging.getLogger(__name__)

POINTER_ACTIONS = {"move", "click", "double_click", "drag"}
AUDIO_ACTIONS = {"speak", "listen"}
MUTATING_ACTIONS = POINTER_ACTIONS | AUDIO_ACTIONS
REQUEST_TIMEOUT = 15.0
TARGET_LOOKBACK_MINUTES = 2

# Per-session consent (process-local; the agent loop runs in this process).
_consents: Dict[str, float] = {}
CONSENT_TTL_SECONDS = 12 * 3600  # safety cap; sessions rarely live longer


def _consent_key(session_id: Optional[str]) -> str:
    return session_id or "_no_session"


def has_consent(session_id: Optional[str]) -> bool:
    granted_at = _consents.get(_consent_key(session_id))
    return bool(granted_at and (time.time() - granted_at) < CONSENT_TTL_SECONDS)


def grant_consent(session_id: Optional[str]) -> None:
    _consents[_consent_key(session_id)] = time.time()


def reset_consents() -> None:
    """Test hook."""
    _consents.clear()


def _consent_required(action: str, session_id: Optional[str]) -> Dict[str, Any]:
    record_audit(CAP_DESKTOP_ACTION, action, session_id=session_id, result="denied")
    return envelope(
        CAP_DESKTOP_ACTION, False, reason="consent_required",
        hint=(
            "Desktop control needs the user's approval for this session. "
            "Ask with the ask_user tool (e.g. 'Allow me to control the mouse "
            "for this session?') and retry with user_approved=true after an "
            "explicit yes."
        ),
    )


def _post_json(url: str, payload: Dict[str, Any], timeout: float = REQUEST_TIMEOUT) -> Tuple[int, Dict[str, Any]]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"reason": str(exc)}
        return exc.code, body if isinstance(body, dict) else {"reason": str(body)}
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return 0, {"reason": str(exc)}


# ── OCR-geometry target resolution ──

_GEOMETRY_KEYS = ("bounding_box", "bbox", "box", "rect")


def _box_center(box: Any) -> Optional[Tuple[int, int]]:
    """Center of an OCR box in any of the common shape dialects."""
    if not isinstance(box, dict):
        return None
    if all(k in box for k in ("left", "top", "width", "height")):
        return int(box["left"] + box["width"] / 2), int(box["top"] + box["height"] / 2)
    if all(k in box for k in ("x", "y", "w", "h")):
        return int(box["x"] + box["w"] / 2), int(box["y"] + box["h"] / 2)
    if all(k in box for k in ("x", "y", "width", "height")):
        return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
    return None


def _extract_matches(body: Any, target_text: str) -> List[Dict[str, Any]]:
    """OCR items matching target_text that carry usable geometry."""
    matches: List[Dict[str, Any]] = []
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return matches
    needle = target_text.lower()
    for item in items:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, dict):
            continue
        # Word/line-level entries (text_json) carry per-fragment geometry.
        fragments = content.get("text_json")
        candidates = fragments if isinstance(fragments, list) else [content]
        for frag in candidates:
            if not isinstance(frag, dict):
                continue
            text = str(frag.get("text") or "")
            if needle not in text.lower():
                continue
            center = None
            for key in _GEOMETRY_KEYS:
                center = _box_center(frag.get(key))
                if center:
                    break
            if center is None:
                center = _box_center(frag)
            matches.append({
                "text": text.strip()[:120],
                "center": center,
                "window": content.get("window_name"),
                "app": content.get("app_name"),
            })
    return matches


def resolve_target(target_text: str) -> Dict[str, Any]:
    """Resolve on-screen text to click coordinates via Screenpipe OCR."""
    params = parse.urlencode({
        "content_type": "ocr",
        "limit": "10",
        "q": target_text,
    })
    req = request.Request(f"{screenpipe_url()}/search?{params}", headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"resolved": False, "reason": f"screenpipe_error: {exc}"}

    matches = _extract_matches(body, target_text)
    if not matches:
        return {"resolved": False, "reason": "target_not_found"}
    with_geometry = [m for m in matches if m["center"]]
    if not with_geometry:
        return {
            "resolved": False, "reason": "target_resolution_unavailable",
            "detail": "OCR matched the text but carries no box geometry — pass x/y coordinates instead.",
            "candidates": matches[:5],
        }
    if len(with_geometry) > 1:
        return {
            "resolved": False, "reason": "ambiguous_target",
            "detail": "Multiple on-screen matches — refine target_text or pass coordinates.",
            "candidates": with_geometry[:5],
        }
    x, y = with_geometry[0]["center"]
    return {"resolved": True, "x": x, "y": y, "match": with_geometry[0]}


# ── Audio actions ──

def _do_listen(session_id: Optional[str]) -> Dict[str, Any]:
    try:
        from services.voice.mic_lease import claim, release
    except Exception:
        return envelope(CAP_DESKTOP_ACTION, False, reason="mic_lease_unavailable")

    lease = claim("operator", mode="ptt", ttl_sec=30)
    if not lease.get("ok"):
        record_audit(CAP_DESKTOP_ACTION, "listen", session_id=session_id, result="denied")
        return envelope(
            CAP_DESKTOP_ACTION, False, reason="mic_busy",
            hint=f"Mic lease held by {lease.get('holder') or 'another session'} — try again later.",
        )
    # Lease acquired, but the worker has no host-side recording endpoint yet.
    # Release immediately and report honestly rather than pretending to listen.
    release("operator", lease.get("token"))
    return envelope(
        CAP_DESKTOP_ACTION, False, degraded=True, reason="unsupported_action",
        hint="Host-side recording lands with the Clicky WPF integration — use the in-app voice input meanwhile.",
    )


def _do_speak(text: str, session_id: Optional[str]) -> Dict[str, Any]:
    if not text:
        return envelope(CAP_DESKTOP_ACTION, False, reason="text_required")
    status, body = _post_json(f"{clicky_worker_url()}/tts", {"text": text})
    if status == 200:
        record_audit(CAP_DESKTOP_ACTION, "speak", target=text[:200], session_id=session_id)
        return envelope(CAP_DESKTOP_ACTION, True, data={"spoken": True, "chars": len(text)})
    if status == 404:
        return envelope(
            CAP_DESKTOP_ACTION, False, degraded=True, reason="unsupported_action",
            hint="This Clicky worker predates TTS — restart it via deploy/scripts/start-clicky.ps1.",
        )
    return degraded_envelope(CAP_DESKTOP_ACTION, f"worker_error: {body.get('reason') or status}")


# ── Entry point ──

def desktop_act(args: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
    action = str(args.get("action") or "").lower().strip()
    if action not in MUTATING_ACTIONS:
        return envelope(
            CAP_DESKTOP_ACTION, False, reason="unknown_action",
            hint=f"Supported: {', '.join(sorted(MUTATING_ACTIONS))}",
        )

    gate = require_capability(CAP_DESKTOP_ACTION)
    if gate:
        gate["reason"] = "clicky_offline"
        return gate

    if args.get("user_approved") is True:
        grant_consent(session_id)
    if not has_consent(session_id):
        return _consent_required(action, session_id)

    if action == "listen":
        return _do_listen(session_id)
    if action == "speak":
        return _do_speak(str(args.get("text") or "").strip(), session_id)

    # Pointer actions.
    x, y = args.get("x"), args.get("y")
    target_desc = None
    if (x is None or y is None) and args.get("target_text"):
        resolution = resolve_target(str(args["target_text"]))
        if not resolution.get("resolved"):
            record_audit(
                CAP_DESKTOP_ACTION, action,
                target=str(args.get("target_text"))[:200],
                session_id=session_id, result="error",
            )
            return envelope(
                CAP_DESKTOP_ACTION, False,
                reason=resolution.get("reason", "target_not_found"),
                data={k: v for k, v in resolution.items() if k in ("candidates", "detail")} or None,
            )
        x, y = resolution["x"], resolution["y"]
        target_desc = f"{args['target_text']} @ ({x},{y})"
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return envelope(
            CAP_DESKTOP_ACTION, False, reason="bad_coordinates",
            hint="Pass integer x/y, or target_text visible on screen.",
        )

    payload: Dict[str, Any] = {"action": action, "x": x, "y": y}
    for key in ("to_x", "to_y", "button"):
        if args.get(key) is not None:
            payload[key] = args[key]

    status, body = _post_json(f"{clicky_worker_url()}/pointer", payload)
    target_desc = target_desc or f"({x},{y})"
    if status == 200 and body.get("ok"):
        record_audit(CAP_DESKTOP_ACTION, action, target=target_desc, session_id=session_id)
        return envelope(CAP_DESKTOP_ACTION, True, data={**body, "target": target_desc})
    record_audit(CAP_DESKTOP_ACTION, action, target=target_desc, session_id=session_id, result="error")
    if status == 404:
        return envelope(
            CAP_DESKTOP_ACTION, False, degraded=True, reason="unsupported_action",
            hint="This Clicky worker predates pointer control — restart it via deploy/scripts/start-clicky.ps1.",
        )
    if status == 0:
        return degraded_envelope(CAP_DESKTOP_ACTION, "clicky_offline")
    return envelope(CAP_DESKTOP_ACTION, False, reason=str(body.get("reason") or f"worker_status_{status}"))
