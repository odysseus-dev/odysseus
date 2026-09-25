#!/usr/bin/env python3
"""Headless overlay Native chat probe.

Do not run ``docker compose exec`` on the Mac. Overlay Docker is the Fusion
guest (``orchestration-vm`` / ``hhpe-forge``). From the Mac repo:

    ./scripts/run_overlay_native_chat_probe.sh

On the guest:

    docker compose -f docker-compose.yml -f docker-compose.openhands.yml \\
      exec -T odysseus python3 /app/scripts/overlay_native_chat_probe.py

Exit 0 only when an Odysseus session + Native chat_stream returns assistant
text (phone path). Prints JSON. Never logs the virtual key or internal token.

``run_odysseus_native_pipe`` is the Hello/Hi step used by
``scripts/overlay_stability_probe.py``. It POSTs Odysseus ``/api/session`` and
``/api/chat_stream`` so uvicorn emits ``overlay.bind`` / ``invoke_agent Native`` /
``openhands.*`` under the synthetic trace. ``run_native_pipe`` remains the
Agent-Server-direct stack check for offline diagnostics.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


NINE = "http://9router:20128"
AGENT = "http://openhands-agent-server:8000"
# Odysseus listens on 7000 inside the container; host APP_BIND only maps the publish.
ODY = "http://127.0.0.1:7000"
KEY_FILE = Path("/opt/odysseus/openhands-native-llm-api-key")
SKIP = ("gpt-6-astra", "-review")
PREFER = ("cx/gpt-5.5", "cx/gpt-5.4", "cx/gpt-5.4-mini")
INTERNAL_HEADER = "X-Odysseus-Internal-Token"
HELLO_MSG = "Hello! Reply with the single word Hi."


def _http(url: str, *, method: str = "GET", body: dict | None = None, headers: dict | None = None, timeout: float = 30) -> tuple[int, dict | str]:
    """HTTP JSON helper. Callers must not put the virtual key into logs."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode() or "{}"
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() if exc.fp else ""
        try:
            return exc.code, json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            return exc.code, raw
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc.reason if getattr(exc, "reason", None) else exc)}


def _assistant_text(events: list) -> str:
    """Join assistant MessageEvent text. User events are ignored."""
    parts: list[str] = []
    for item in events:
        if item.get("kind") != "MessageEvent" or item.get("source") == "user":
            continue
        llm_message = item.get("llm_message") or {}
        content = item.get("content") or llm_message.get("content") or ""
        if isinstance(content, str) and content.strip():
            parts.append(content)
        elif isinstance(content, list):
            parts.append(
                "".join(
                    block.get("text") or ""
                    for block in content
                    if isinstance(block, dict)
                )
            )
    return "".join(parts).strip()


def _pick_catalog_id(ids: list[str]) -> str:
    """Prefer cx/gpt-5.5. Skip gpt-6-astra and -review. Never pin openai/auto."""
    chosen = next((item for item in PREFER if item in ids), "")
    if not chosen:
        chosen = next((item for item in ids if not any(skip in item for skip in SKIP)), ids[0] if ids else "")
    return chosen


def _read_sidecar_key() -> str:
    """Return the sidecar virtual key, or empty. Callers must not log it."""
    if not KEY_FILE.is_file():
        return ""
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _odysseus_auth_headers() -> dict[str, str]:
    """Loopback internal-tool header plus W3C traceparent when a span is active.

    Agents: ``ODYSSEUS_INTERNAL_TOKEN`` must match uvicorn (compose env). Without
    it AUTH_ENABLED rejects /api/session. ``inject`` continues the probe's
    ``overlay.stability`` trace into FastAPI so Tempo shows one tree.
    """
    token = os.environ.get("ODYSSEUS_INTERNAL_TOKEN", "").strip()
    headers: dict[str, str] = {}
    if token:
        headers[INTERNAL_HEADER] = token
    try:
        from opentelemetry.propagate import inject

        inject(headers)
    except Exception:
        # OTel optional in unit tests that only monkeypatch form/sse helpers.
        pass
    return headers


def _http_form(
    url: str,
    *,
    fields: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30,
) -> tuple[int, dict | str]:
    """POST ``application/x-www-form-urlencoded`` (phone FormData shaped as form)."""
    encoded = urllib.parse.urlencode({k: str(v) for k, v in (fields or {}).items()}).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode() or "{}"
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() if getattr(exc, "fp", None) else ""
        try:
            return exc.code, json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            return exc.code, raw


def _http_sse_events(
    url: str,
    *,
    fields: dict | None = None,
    headers: dict | None = None,
    timeout: float = 120,
) -> tuple[int, list[dict]]:
    """POST form body and parse ``data:`` JSON events until ``[DONE]`` or EOF."""
    encoded = urllib.parse.urlencode({k: str(v) for k, v in (fields or {}).items()}).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "text/event-stream")
    events: list[dict] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            while True:
                line = response.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text or text.startswith(":"):
                    continue
                if text.startswith("data:"):
                    payload = text[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        item = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        events.append(item)
            return status, events
    except urllib.error.HTTPError as exc:
        return exc.code, [{"error": (exc.read().decode() if getattr(exc, "fp", None) else str(exc))[:400]}]


def run_odysseus_native_pipe() -> dict:
    """Phone path: Odysseus session create + Native chat_stream Hello/Hi.

    Returns ``{ok, session_id, openhands_conversation_id, steps}``.
    ``session_id`` is the Odysseus conversation id (``gen_ai.conversation.id``).
    Auth is ``ODYSSEUS_INTERNAL_TOKEN`` on loopback; never printed.
    """
    report: dict = {
        "ok": False,
        "session_id": None,
        "openhands_conversation_id": None,
        "steps": [],
    }
    token = os.environ.get("ODYSSEUS_INTERNAL_TOKEN", "").strip()
    if not token:
        report["error"] = "ODYSSEUS_INTERNAL_TOKEN unset"
        return report
    headers = _odysseus_auth_headers()

    status, created = _http_form(
        f"{ODY}/api/session",
        fields={
            "name": "overlay-stability",
            "model": "automatic",
            "endpoint_url": "",
            "skip_validation": "true",
        },
        headers=headers,
        timeout=30,
    )
    sid = created.get("id") if isinstance(created, dict) else None
    report["steps"].append({
        "name": "session",
        "http": status,
        "session_id": sid,
        "model": (created.get("model") if isinstance(created, dict) else None),
    })
    if status not in {200, 201} or not sid:
        report["error"] = "session create failed"
        return report
    report["session_id"] = str(sid)

    status, events = _http_sse_events(
        f"{ODY}/api/chat_stream",
        fields={
            "message": HELLO_MSG,
            "session": str(sid),
            "selected_model": "automatic",
            "agent_profile_id": "odysseus",
            "mode": "chat",
        },
        headers=headers,
        timeout=120,
    )
    text_parts: list[str] = []
    oh_cid = None
    for item in events:
        if item.get("type") == "execution" and item.get("conversation_id"):
            oh_cid = str(item["conversation_id"])
        delta = item.get("delta")
        if isinstance(delta, str) and delta and not item.get("thinking"):
            text_parts.append(delta)
        if item.get("error"):
            report["steps"].append({"name": "chat_stream", "http": status, "error": item.get("error")})
            return report
    text = "".join(text_parts).strip()
    report["openhands_conversation_id"] = oh_cid
    report["steps"].append({
        "name": "chat_stream",
        "http": status,
        "openhands_conversation_id": oh_cid,
        "text": text[:400],
        "event_count": len(events),
    })
    report["ok"] = status == 200 and bool(text) and bool(oh_cid)
    if not report["ok"] and not report.get("error"):
        report["error"] = "chat_stream missing assistant text or OpenHands id"
    return report


def run_native_pipe() -> dict:
    """One-word 9router completion plus OpenHands Hello/Hi (Agent Server direct).

    Returns ``{ok, session_id, steps}`` and an ``error`` string when the
    sidecar is missing. The virtual key is never copied into the dict.
    ``session_id`` is the Agent Server conversation id. Prefer
    ``run_odysseus_native_pipe`` for stability / Tempo session-tree DoD.
    """
    report: dict = {"ok": False, "session_id": None, "steps": []}
    key = _read_sidecar_key()
    if not KEY_FILE.is_file():
        report["error"] = "native-llm-api-key sidecar missing"
        return report
    if not key:
        report["error"] = "native-llm-api-key empty"
        return report
    auth = {"Authorization": f"Bearer {key}"}

    status, models = _http(f"{NINE}/v1/models", headers=auth)
    ids = [str(item.get("id") or "") for item in (models.get("data") or [])] if isinstance(models, dict) else []
    ids = [item for item in ids if item]
    catalog_id = _pick_catalog_id(ids)
    litellm_id = catalog_id if catalog_id.startswith("openai/") else f"openai/{catalog_id}"
    report["steps"].append({
        "name": "catalog",
        "http": status,
        "count": len(ids),
        "catalog_id": catalog_id,
        "litellm_id": litellm_id,
    })
    if status != 200 or not catalog_id:
        return report

    status, completion = _http(
        f"{NINE}/v1/chat/completions",
        method="POST",
        headers=auth,
        body={
            "model": catalog_id,
            "messages": [{"role": "user", "content": "Say hi in one word."}],
            "max_tokens": 8,
        },
        timeout=45,
    )
    reply = ""
    if isinstance(completion, dict):
        choices = completion.get("choices") or []
        if choices:
            reply = str((choices[0].get("message") or {}).get("content") or "")
    report["steps"].append({
        "name": "completions",
        "http": status,
        "reply": reply[:200],
        "error": (completion.get("error") if isinstance(completion, dict) else completion),
    })
    if status != 200 or not reply.strip():
        return report

    status, created = _http(
        f"{AGENT}/api/conversations",
        method="POST",
        body={
            "workspace": {"working_dir": "/workspace", "kind": "LocalWorkspace"},
            "agent_settings": {
                "schema_version": 5,
                "agent_kind": "openhands",
                "agent": "CodeActAgent",
                "llm": {
                    "model": litellm_id,
                    "api_key": key,
                    "base_url": f"{NINE}/v1",
                    "auth_type": "api_key",
                    "api_mode": "chat",
                },
            },
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": HELLO_MSG}],
            },
            "autotitle": False,
        },
        timeout=30,
    )
    cid = created.get("id") if isinstance(created, dict) else None
    report["session_id"] = str(cid) if cid else None
    report["steps"].append({"name": "create", "http": status, "conversation_id": cid})
    if status not in {200, 201} or not cid:
        return report

    text = ""
    exec_status = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        _, info = _http(f"{AGENT}/api/conversations/{cid}")
        exec_status = str((info or {}).get("execution_status") or "")
        _, events = _http(f"{AGENT}/api/conversations/{cid}/events/search")
        items = (events or {}).get("items") if isinstance(events, dict) else []
        text = _assistant_text(items or [])
        if exec_status in {"finished", "completed"} and text:
            break
        if exec_status in {"error", "failed", "errored"}:
            break
        time.sleep(1)
    report["steps"].append({
        "name": "openhands",
        "execution_status": exec_status,
        "text": text[:400],
    })
    report["ok"] = bool(text) and exec_status in {"finished", "completed"}
    return report


def main() -> int:
    """Print the Odysseus phone-path report. Exit 0 only when ``ok`` is true."""
    report = run_odysseus_native_pipe()
    public = {
        "ok": report["ok"],
        "session_id": report.get("session_id"),
        "openhands_conversation_id": report.get("openhands_conversation_id"),
        "steps": report["steps"],
    }
    if report.get("error"):
        public["error"] = report["error"]
    print(json.dumps(public, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
