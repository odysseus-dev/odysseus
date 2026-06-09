"""Cookbook serve lifecycle: kills scheduler-owned serves whose end-of-
window has passed.

Pairs with action_cookbook_serve in builtin_actions.py — that action
stamps the task it launches with `_scheduledStopAtMs`, this loop ticks
every 60s and kills any serve whose stamp is in the past.

Single small module. Delete this file + the registration line in app.py
and the feature stops doing anything; scheduler-launched serves just
stay up until the user kills them manually.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
from core.constants import internal_api_base
from src.constants import COOKBOOK_STATE_FILE

logger = logging.getLogger(__name__)


def _internal_headers() -> dict:
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    return {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}


async def _delete_endpoint_for_task(task: dict) -> None:
    """Drop the auto-registered model endpoint for a scheduled-stop serve.

    Without this, killing the tmux session leaves the endpoint sitting in
    the picker (probe goes offline; chats still try to route there) and
    the user has to delete it by hand in Settings -> Endpoints.
    """
    import re as _re
    payload = task.get("payload") or {}
    cmd = str(payload.get("_cmd") or "")
    remote = task.get("remoteHost") or ""
    # Build host the same way _auto_register_llm_endpoint does so URL match wins.
    if remote:
        host = remote.split("@")[-1] if "@" in remote else remote
    else:
        host = "host.docker.internal"
    port_match = _re.search(r"--port\s+(\d+)", cmd)
    ollama_host_match = _re.search(r"OLLAMA_HOST=[^\s]*?:(\d+)", cmd)
    if port_match:
        port = int(port_match.group(1))
    elif ollama_host_match:
        port = int(ollama_host_match.group(1))
    elif "ollama" in cmd:
        port = 11434
    else:
        port = 8080
    base_url = f"http://{host}:{port}/v1"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{internal_api_base()}/api/model-endpoints",
                headers=_internal_headers(),
            )
            if r.status_code >= 400:
                return
            eps = r.json() if r.content else []
            # Prefer exact URL match; fall back to host:port substring so we
            # still catch the case where 0.0.0.0 vs the registered host
            # representation diverged.
            ep = next((e for e in eps if e.get("base_url") == base_url), None)
            if not ep:
                hostport = f"{host}:{port}"
                ep = next((e for e in eps if hostport in (e.get("base_url") or "")), None)
            if ep:
                await client.delete(
                    f"{internal_api_base()}/api/model-endpoints/{ep['id']}",
                    headers=_internal_headers(),
                )
                logger.info(
                    f"cookbook_serve_lifecycle: deleted endpoint {ep.get('id')} "
                    f"({ep.get('base_url')}) after scheduled stop"
                )
    except Exception as e:
        logger.warning(f"cookbook_serve_lifecycle: endpoint delete failed: {e}")


async def _stop_serve(session_id: str, remote_host: str = "", ssh_port: str = "") -> bool:
    """Kill the tmux session that hosts the serve.

    There's no `/api/model/stop` route — the cookbook UI and the chat
    agent both kill via `/api/shell/exec` running a `tmux kill-session`
    (wrapped in ssh for remote hosts). Mirror that here so the
    lifecycle loop can actually stop scheduler-launched serves at
    window-end. Without this, the action stamped `_scheduledStopAtMs`
    correctly but every kill attempt failed silently (the route
    returned 404 and the result was logged as "failed").
    """
    import shlex
    if remote_host:
        port_flag = f"-p {shlex.quote(str(ssh_port))} " if ssh_port and str(ssh_port) != "22" else ""
        cmd = (
            f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
            f"{port_flag}{shlex.quote(remote_host)} "
            f"'tmux kill-session -t {shlex.quote(session_id)}'"
        )
    else:
        cmd = f"tmux kill-session -t {shlex.quote(session_id)}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{internal_api_base()}/api/shell/exec",
                json={"command": cmd},
                headers=_internal_headers(),
            )
            if r.status_code >= 400:
                return False
            data = r.json() if r.content else {}
            ec = data.get("exit_code")
            # tmux returns non-zero when the session is already gone
            # ("can't find session: ..."). That's still "stop succeeded"
            # from our POV — the goal is no live session at the end.
            if ec in (None, 0):
                return True
            stderr = (data.get("stderr") or "").lower()
            return "no server" in stderr or "can't find session" in stderr or "session not found" in stderr
    except Exception as e:
        logger.warning(f"cookbook_serve_lifecycle: stop {session_id} failed: {e}")
        return False


async def _tick() -> None:
    state_path = Path(COOKBOOK_STATE_FILE)
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    tasks = state.get("tasks") or []
    now_ms = int(time.time() * 1000)
    to_stop = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        stop_at = t.get("_scheduledStopAtMs")
        if not isinstance(stop_at, (int, float)):
            continue
        if stop_at > now_ms:
            continue
        if (t.get("status") or "").lower() in {"stopped", "ended", "killed", "crashed"}:
            continue
        sid = t.get("sessionId") or t.get("id")
        if not sid:
            continue
        to_stop.append((sid, t.get("remoteHost") or "", t.get("sshPort") or ""))
    if not to_stop:
        return
    # Re-read state once before writing so we capture any updates from
    # concurrent UI syncs.
    stopped_any = False
    for sid, host, port in to_stop:
        ok = await _stop_serve(sid, host, port)
        logger.info(f"cookbook_serve_lifecycle: stop {sid} (host={host or 'local'}): {'ok' if ok else 'failed'}")
        if ok:
            stopped_any = True
            # Drop the auto-registered endpoint so the model picker and
            # the chat router don't keep pointing at a dead server.
            for t in tasks:
                if isinstance(t, dict) and (t.get("sessionId") == sid or t.get("id") == sid):
                    if t.get("type") == "serve":
                        await _delete_endpoint_for_task(t)
                    t["status"] = "stopped"
                    t["_scheduledStopAtMs"] = None
                    t["_lastStatusFlipAt"] = now_ms
                    break
    if stopped_any:
        try:
            from core.atomic_io import atomic_write_json
            state["tasks"] = tasks
            atomic_write_json(state_path, state)
        except Exception as e:
            logger.warning(f"cookbook_serve_lifecycle: state write failed: {e}")


async def cookbook_serve_lifecycle_loop() -> None:
    """Forever-loop. Registered as a startup task in app.py."""
    await asyncio.sleep(20)  # let the rest of startup settle
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.warning(f"cookbook_serve_lifecycle tick failed: {e}")
        await asyncio.sleep(60)
