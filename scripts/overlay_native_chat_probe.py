#!/usr/bin/env python3
"""Headless overlay Native chat probe.

Do not run ``docker compose exec`` on the Mac. Overlay Docker is the Fusion
guest (``orchestration-vm`` / ``hhpe-forge``). From the Mac repo:

    ./scripts/run_overlay_native_chat_probe.sh

On the guest:

    docker compose -f docker-compose.yml -f docker-compose.openhands.yml \\
      exec -T odysseus python3 /app/scripts/overlay_native_chat_probe.py

Exit 0 only when 9router completions and an OpenHands conversation both return
assistant text. Prints JSON. Never logs the virtual key.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path


NINE = "http://9router:20128"
AGENT = "http://openhands-agent-server:8000"
KEY_FILE = Path("/opt/odysseus/openhands-native-llm-api-key")
SKIP = ("gpt-6-astra", "-review")
PREFER = ("cx/gpt-5.5", "cx/gpt-5.4", "cx/gpt-5.4-mini")


def _http(url: str, *, method: str = "GET", body: dict | None = None, headers: dict | None = None, timeout: float = 30) -> tuple[int, dict | str]:
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


def _assistant_text(events: list) -> str:
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
    chosen = next((item for item in PREFER if item in ids), "")
    if not chosen:
        chosen = next((item for item in ids if not any(skip in item for skip in SKIP)), ids[0] if ids else "")
    return chosen


def main() -> int:
    report: dict = {"ok": False, "steps": []}
    if not KEY_FILE.is_file():
        report["error"] = "native-llm-api-key sidecar missing"
        print(json.dumps(report, indent=2))
        return 1
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        report["error"] = "native-llm-api-key empty"
        print(json.dumps(report, indent=2))
        return 1
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
        print(json.dumps(report, indent=2))
        return 1

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
        print(json.dumps(report, indent=2))
        return 1

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
                "content": [{"type": "text", "text": "Hello! Reply with the single word Hi."}],
            },
            "autotitle": False,
        },
        timeout=30,
    )
    cid = created.get("id") if isinstance(created, dict) else None
    report["steps"].append({"name": "create", "http": status, "conversation_id": cid})
    if status not in {200, 201} or not cid:
        print(json.dumps(report, indent=2))
        return 1

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
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
