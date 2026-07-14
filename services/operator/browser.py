"""Browser action — CDP control of the user's already-running Chrome.

Backs the `browser_act` agent tool. Attaches to Chrome's DevTools endpoint
(default http://127.0.0.1:9222); never launches a headless browser. Read-only
actions (tabs, snapshot) are ungated; mutating actions (navigate, click, type,
evaluate) are consent-gated per session and audited, sharing the consent store
with desktop_act.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from urllib import error, request

from services.operator.cdp import CdpSession
from services.operator.core import (
    CAP_BROWSER_ACTION,
    cdp_url,
    consent_required_envelope,
    degraded_envelope,
    envelope,
    grant_consent,
    has_consent,
    record_audit,
)

logger = logging.getLogger(__name__)

READ_ACTIONS = {"tabs", "snapshot"}
MUTATING_ACTIONS = {"navigate", "click", "type", "evaluate"}
ALL_ACTIONS = READ_ACTIONS | MUTATING_ACTIONS
REQUEST_TIMEOUT = 10.0

CDP_HINT = "Start Chrome with --remote-debugging-port=9222 (close existing Chrome first)."


def _list_targets() -> Optional[List[Dict[str, Any]]]:
    """GET /json/list — open pages, or None if the endpoint is unreachable."""
    req = request.Request(f"{cdp_url()}/json/list", headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    return [t for t in data if t.get("type") == "page"]


def _pick_target(targets: List[Dict[str, Any]], target_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if target_id:
        return next((t for t in targets if t.get("id") == target_id), None)
    return targets[0] if targets else None


def _tabs_payload(targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "tabs": [
            {"id": t.get("id"), "title": t.get("title"), "url": t.get("url")}
            for t in targets
        ],
        "count": len(targets),
    }


def _unreachable() -> Dict[str, Any]:
    result = degraded_envelope(CAP_BROWSER_ACTION, "cdp_unreachable", hint=CDP_HINT)
    return result


def _run_on_target(target: Dict[str, Any], fn) -> Dict[str, Any]:
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return degraded_envelope(CAP_BROWSER_ACTION, "no_ws_url", hint=CDP_HINT)
    try:
        with CdpSession(ws_url, timeout=REQUEST_TIMEOUT * 2) as session:
            return fn(session)
    except (ConnectionError, TimeoutError, OSError) as exc:
        return degraded_envelope(CAP_BROWSER_ACTION, f"cdp_error: {exc}", hint=CDP_HINT)
    except RuntimeError as exc:  # CDP-reported command error
        return envelope(CAP_BROWSER_ACTION, False, reason=str(exc))


def _do_snapshot(session: CdpSession) -> Dict[str, Any]:
    session.command("DOM.enable")
    doc = session.command("DOM.getDocument", {"depth": -1, "pierce": True})
    outline = session.command(
        "Runtime.evaluate",
        {
            "expression": _SNAPSHOT_JS,
            "returnByValue": True,
        },
    )
    value = (outline.get("result") or {}).get("value")
    return envelope(CAP_BROWSER_ACTION, True, data={"snapshot": value, "root_node": bool(doc)})


# Accessibility-ish outline: interactive elements with a stable ref, cheap to
# compute in-page and far smaller than a raw DOM dump for the model to read.
_SNAPSHOT_JS = r"""
(() => {
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[onclick]';
  const nodes = [...document.querySelectorAll(sel)].slice(0, 200);
  return nodes.map((el, i) => {
    const r = el.getBoundingClientRect();
    return {
      ref: 'n' + i,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || null,
      text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80),
      name: el.getAttribute('name') || el.id || null,
      visible: r.width > 0 && r.height > 0,
    };
  }).filter(n => n.visible);
})()
"""


def _resolve_ref_selector(session: CdpSession, ref_or_selector: str) -> str:
    """Map a snapshot ref (n12) back to a selector, else pass through."""
    if ref_or_selector and ref_or_selector.startswith("n") and ref_or_selector[1:].isdigit():
        idx = int(ref_or_selector[1:])
        expr = (
            "(() => { const sel='a,button,input,textarea,select,[role=button],"
            "[role=link],[onclick]'; const el=[...document.querySelectorAll(sel)]"
            f"[{idx}]; if(!el) return null; if(el.id) return '#'+CSS.escape(el.id); "
            "return el.tagName.toLowerCase(); })()"
        )
        result = session.command("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        value = (result.get("result") or {}).get("value")
        if value:
            return value
    return ref_or_selector


def _do_click(session: CdpSession, selector: str) -> Dict[str, Any]:
    selector = _resolve_ref_selector(session, selector)
    expr = (
        f"(() => {{ const el=document.querySelector({json.dumps(selector)}); "
        "if(!el) return false; el.click(); return true; }})()"
    )
    result = session.command("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    clicked = bool((result.get("result") or {}).get("value"))
    if not clicked:
        return envelope(CAP_BROWSER_ACTION, False, reason="element_not_found", data={"selector": selector})
    return envelope(CAP_BROWSER_ACTION, True, data={"clicked": selector})


def _do_type(session: CdpSession, selector: str, text: str) -> Dict[str, Any]:
    selector = _resolve_ref_selector(session, selector)
    expr = (
        f"(() => {{ const el=document.querySelector({json.dumps(selector)}); "
        "if(!el) return false; el.focus(); "
        f"el.value={json.dumps(text)}; "
        "el.dispatchEvent(new Event('input',{bubbles:true})); "
        "el.dispatchEvent(new Event('change',{bubbles:true})); return true; }})()"
    )
    result = session.command("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    ok = bool((result.get("result") or {}).get("value"))
    if not ok:
        return envelope(CAP_BROWSER_ACTION, False, reason="element_not_found", data={"selector": selector})
    return envelope(CAP_BROWSER_ACTION, True, data={"typed_into": selector, "chars": len(text)})


def _do_evaluate(session: CdpSession, expression: str) -> Dict[str, Any]:
    result = session.command(
        "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    value = (result.get("result") or {}).get("value")
    return envelope(CAP_BROWSER_ACTION, True, data={"value": value})


def _do_navigate(session: CdpSession, url: str) -> Dict[str, Any]:
    session.command("Page.enable")
    result = session.command("Page.navigate", {"url": url})
    if result.get("errorText"):
        return envelope(CAP_BROWSER_ACTION, False, reason=f"navigation_failed: {result['errorText']}")
    return envelope(CAP_BROWSER_ACTION, True, data={"navigated_to": url, "frame_id": result.get("frameId")})


def browser_act(args: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
    action = str(args.get("action") or "").lower().strip()
    if action not in ALL_ACTIONS:
        return envelope(
            CAP_BROWSER_ACTION, False, reason="unknown_action",
            hint=f"Supported: {', '.join(sorted(ALL_ACTIONS))}",
        )

    targets = _list_targets()
    if targets is None:
        return _unreachable()

    # Consent only for mutating actions; reads pass through.
    if action in MUTATING_ACTIONS:
        if args.get("user_approved") is True:
            grant_consent(session_id)
        if not has_consent(session_id):
            record_audit(CAP_BROWSER_ACTION, action, session_id=session_id, result="denied")
            return consent_required_envelope(CAP_BROWSER_ACTION)

    if action == "tabs":
        return envelope(CAP_BROWSER_ACTION, True, data=_tabs_payload(targets))

    target = _pick_target(targets, args.get("target_id"))
    if target is None:
        return envelope(CAP_BROWSER_ACTION, False, reason="no_open_tab", hint="Open a tab in Chrome first.")

    if action == "snapshot":
        return _run_on_target(target, _do_snapshot)

    if action == "navigate":
        url = str(args.get("url") or "").strip()
        if not url:
            return envelope(CAP_BROWSER_ACTION, False, reason="url_required")
        result = _run_on_target(target, lambda s: _do_navigate(s, url))
        _audit(action, url, session_id, result)
        return result

    if action == "click":
        selector = str(args.get("selector") or args.get("ref") or "").strip()
        if not selector:
            return envelope(CAP_BROWSER_ACTION, False, reason="selector_required")
        result = _run_on_target(target, lambda s: _do_click(s, selector))
        _audit(action, selector, session_id, result)
        return result

    if action == "type":
        selector = str(args.get("selector") or args.get("ref") or "").strip()
        text = str(args.get("text") or "")
        if not selector:
            return envelope(CAP_BROWSER_ACTION, False, reason="selector_required")
        result = _run_on_target(target, lambda s: _do_type(s, selector, text))
        _audit(action, selector, session_id, result)
        return result

    if action == "evaluate":
        expression = str(args.get("expression") or "").strip()
        if not expression:
            return envelope(CAP_BROWSER_ACTION, False, reason="expression_required")
        result = _run_on_target(target, lambda s: _do_evaluate(s, expression))
        _audit(action, expression[:200], session_id, result)
        return result

    return envelope(CAP_BROWSER_ACTION, False, reason="unhandled_action")


def _audit(action: str, target: str, session_id: Optional[str], result: Dict[str, Any]) -> None:
    record_audit(
        CAP_BROWSER_ACTION, action, target=target, session_id=session_id,
        result="ok" if result.get("ok") else "error",
    )
