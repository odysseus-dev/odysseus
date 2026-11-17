"""Read-only local serving inventory. Discovery never implies process ownership.

No generation, model loads, pulls, installs, signals or automatic recovery.
Persisted Cookbook tasks and PID/start-token receipts survive app restarts.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx

from core.platform_compat import pid_alive
from src.process_ownership import UNVERIFIABLE, verify

_SESSION = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_LOOPBACK_BINDS = {"localhost", "127.0.0.1", "0.0.0.0", "::", "::1"}


def local_serving_target(task: dict) -> dict | None:
    """Extract a local native runtime target, without executing its command."""
    if task.get("type") != "serve" or task.get("remoteHost"):
        return None
    payload = task.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    if payload.get("remote_host"):
        return None
    cmd = str(payload.get("_cmd") or "")
    if re.search(r"\b(?:docker|podman|ssh|wsl)\b", cmd):
        return None
    if re.search(r"\bollama\s+serve\b", cmd):
        engine, port, host = "ollama", 11434, "127.0.0.1"
        bind = re.search(r"(?:^|\s)OLLAMA_HOST=['\"]?([^\s'\"]+)", cmd)
        if bind:
            try:
                url = urlsplit(bind[1] if "://" in bind[1] else "http://" + bind[1])
                if url.scheme != "http" or url.hostname not in _LOOPBACK_BINDS:
                    return None
                host = "::1" if ":" in url.hostname else "127.0.0.1"
                port = url.port or 11434
            except ValueError:
                return None
    elif re.search(r"\bllama-server(?:\.exe)?\b", cmd):
        engine, port, host = "llamacpp", 8080, "127.0.0.1"
        match = re.search(r"(?:^|\s)(?:--port|-p)(?:=|\s+)(\d+)(?=\s|$)", cmd)
        if match:
            port = int(match[1])
    else:
        return None
    if not 1 <= port <= 65535:
        return None
    bracketed = f"[{host}]" if ":" in host else host
    return {"engine": engine, "base_url": f"http://{bracketed}:{port}"}


def task_process_identity(task: dict, log_dir: Path) -> dict:
    sid = str(task.get("sessionId") or "")
    result = {"pid": None, "process_ownership": UNVERIFIABLE}
    if not _SESSION.fullmatch(sid):
        return result
    try:
        pid = int((log_dir / f"{sid}.pid").read_text(encoding="utf-8").strip())
        if pid <= 0:
            return result
        result["pid"] = pid
        # On Windows an exited process may retain a readable creation token
        # while another handle remains open. Token equality is not liveness.
        if not pid_alive(pid):
            result["process_ownership"] = "gone"
            return result
        token = (log_dir / f"{sid}.pid.token").read_text(encoding="utf-8").strip()
        if token:
            result["process_ownership"] = verify(pid, token)
    except (OSError, ValueError):
        pass
    return result


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[int | None, dict | None]:
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return None, None
    try:
        data = response.json()
    except ValueError:
        data = None
    return response.status_code, data if isinstance(data, dict) else None


def _records(data: dict | None, key: str) -> list[dict]:
    values = (data or {}).get(key)
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


async def probe_local_runtime(client: httpx.AsyncClient, engine: str, base_url: str) -> dict:
    """Probe metadata only. Public helpers also refuse non-loopback URLs."""
    url = urlsplit(base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"} or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Serving probes require a plain loopback HTTP origin")
    result = {"readiness": "unavailable", "models": [], "loaded_models": [], "context_length": None,
              "capabilities": {"modalities": None, "chat_template": None}}
    if engine == "llamacpp":
        (code, health), (_, models), (_, props) = await asyncio.gather(
            _get_json(client, base_url + "/health"),
            _get_json(client, base_url + "/models"),
            _get_json(client, base_url + "/props"),
        )
        if code == 503:
            result["readiness"] = "loading"
        elif code == 200 and health and health.get("status") == "ok":
            result["readiness"] = "ready"
        elif code is not None:
            result["readiness"] = "error"
        result["models"] = [m["id"] for m in _records(models, "data") if isinstance(m.get("id"), str)]
        if (props or {}).get("is_sleeping") is True and result["readiness"] == "ready":
            result["readiness"] = "sleeping"
        for source, key in (("modalities", "modalities"), ("chat_template_caps", "chat_template")):
            value = (props or {}).get(source)
            if isinstance(value, dict):
                result["capabilities"][key] = value
        settings = (props or {}).get("default_generation_settings") or {}
        ctx = settings.get("n_ctx") if isinstance(settings, dict) else None
        if isinstance(ctx, int) and ctx > 0:
            result["context_length"] = ctx
    elif engine == "ollama":
        (code, version), (_, models), (_, loaded) = await asyncio.gather(
            _get_json(client, base_url + "/api/version"),
            _get_json(client, base_url + "/api/tags"),
            _get_json(client, base_url + "/api/ps"),
        )
        if code == 200 and version and isinstance(version.get("version"), str):
            result["readiness"] = "ready"
            result["version"] = version["version"]
        elif code is not None:
            result["readiness"] = "error"
        result["models"] = [m["name"] for m in _records(models, "models") if isinstance(m.get("name"), str)]
        result["loaded_models"] = [
            {k: m[k] for k in ("name", "context_length", "size", "size_vram", "expires_at") if k in m}
            for m in _records(loaded, "models")
        ]
    else:
        raise ValueError("Unknown serving engine")
    return result


def read_local_services(state_path: Path, log_dir: Path) -> list[dict]:
    """Recover receipts, not processes: no new server is launched on restart."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    tasks = state.get("tasks", []) if isinstance(state, dict) else []
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    from src.model_serving_control import LocalServingController
    controller = LocalServingController(state_path, log_dir)
    services = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict):
            continue
        sid = str(task.get("sessionId") or "")
        plan = controller.recorded_plan(sid)
        target = ({"engine": plan["engine"], "base_url": plan["base_url"]} if plan
                  else local_serving_target(task))
        if target is None:
            payload = task.get("payload") or {}
            cmd = str(payload.get("_cmd") or "") if isinstance(payload, dict) else ""
            if (task.get("type") == "serve" and not task.get("remoteHost")
                    and isinstance(payload, dict) and not payload.get("remote_host")
                    and re.search(r"\bllama_cpp\.server\b", cmd)
                    and not re.search(r"\b(?:docker|podman|ssh|wsl)\b", cmd)):
                target = {"engine": "llama_cpp_python", "base_url": None}
            else:
                continue
        identity = task_process_identity(task, log_dir)
        management = ("odysseus-managed" if identity["process_ownership"] == "owned" else "stale") if plan else "legacy-recorded"
        if target["engine"] == "llama_cpp_python":
            management = "unmanaged-python"
        services.append({
            "id": sid, "task_status": task.get("status"), "management": management,
            "source": "cookbook", "exit": controller.exit_details(sid) if plan else None, **target, **identity,
        })
    known = {service["id"] for service in services}
    for file in log_dir.glob("serve-*.serving.json"):
        sid = file.name.removesuffix(".serving.json")
        if sid in known or not controller.control_permission(sid):
            continue
        plan = controller.recorded_plan(sid)
        if plan:
            services.append({"id": sid, "source": "receipt", "task_status": "running",
                "engine": plan["engine"], "base_url": plan["base_url"],
                "management": "odysseus-managed", "exit": None,
                **task_process_identity({"sessionId": sid}, log_dir)})
    if not any(s["engine"] == "ollama" and s["base_url"] == "http://127.0.0.1:11434" for s in services):
        services.append({
            "id": "external-ollama", "source": "discovery", "engine": "ollama",
            "base_url": "http://127.0.0.1:11434", "pid": None,
            "process_ownership": "external", "task_status": None, "management": "external", "exit": None,
        })
    return services


def lifecycle_state(ownership: str, task_status: str | None, readiness: str) -> str:
    if ownership == "external":
        return "external" if readiness == "ready" else readiness
    if ownership == "foreign":
        return "ownership-lost"
    if ownership == UNVERIFIABLE:
        return "unverifiable"
    cancelled = task_status in {"cancel_requested", "stopped", "canceled", "cancelled", "ended", "killed"}
    if ownership == "gone":
        return "stopped" if cancelled else "crashed"
    if cancelled:
        return "stopping"
    return "unresponsive" if readiness == "unavailable" else readiness


async def wait_for_managed_runtime(state_path: Path, log_dir: Path, sid: str, timeout: float = 30) -> dict:
    """Bound this observation, never terminate/restart a slow or external runtime."""
    from src.model_serving_control import LocalServingController
    if not 0 < timeout <= 300:
        raise ValueError('Readiness timeout must be positive and at most 300 seconds')
    controller = LocalServingController(state_path, log_dir)
    plan = await asyncio.to_thread(controller.recorded_plan, sid)
    if not plan:
        raise ValueError('A controller launch receipt is required for readiness wait')
    deadline = time.monotonic() + timeout
    runtime = {'readiness': 'unavailable'}

    async def identity_state():
        try:
            identity = await asyncio.to_thread(task_process_identity, {'sessionId': sid}, log_dir)
            state = await asyncio.to_thread(controller._state)
            status = next((row.get('status') for row in state.get('tasks', [])
                           if isinstance(row, dict) and row.get('sessionId') == sid), None)
            return identity, status
        except (OSError, ValueError):
            return {'pid': None, 'process_ownership': 'unverifiable',
                    'error': 'Identity or task state could not be read'}, None

    def interrupted(identity, status):
        return (identity['process_ownership'] != 'owned' or
                status in {'cancel_requested', 'stopped', 'canceled', 'cancelled', 'ended', 'killed'})

    async with httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False) as client:
        while True:
            identity, status = await identity_state()
            if interrupted(identity, status):
                return {'ok': False, 'session_id': sid, **identity, **runtime,
                        'state': lifecycle_state(identity['process_ownership'], status, runtime['readiness']),
                        'endpoint_ownership_verified': False, 'forced': False}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                runtime = await asyncio.wait_for(probe_local_runtime(client, plan['engine'], plan['base_url']), remaining)
            except asyncio.TimeoutError:
                break
            # A responsive replacement endpoint cannot conceal exit/cancel while probing.
            current, status = await identity_state()
            if current != identity or interrupted(current, status):
                return {'ok': False, 'session_id': sid, **current, **runtime,
                        'state': lifecycle_state(current['process_ownership'], status, runtime['readiness']),
                        'endpoint_ownership_verified': False, 'forced': False}
            if runtime['readiness'] in {'ready', 'sleeping'}:
                return {'ok': True, 'session_id': sid, **current, **runtime,
                        'state': runtime['readiness'], 'base_url': plan['base_url'],
                        'endpoint_ownership_verified': False}
            await asyncio.sleep(min(.1, max(0, deadline - time.monotonic())))
    current, status = await identity_state()
    if current != identity or interrupted(current, status):
        return {'ok': False, 'session_id': sid, **current, **runtime,
                'state': lifecycle_state(current['process_ownership'], status, runtime['readiness']),
                'endpoint_ownership_verified': False, 'forced': False}
    return {'ok': False, 'session_id': sid, **identity, **runtime,
            'state': 'startup-timeout', 'endpoint_ownership_verified': False, 'forced': False}


async def local_serving_status(state_path: Path, log_dir: Path) -> dict:
    from src.model_serving_control import LocalServingController
    controller = LocalServingController(state_path, log_dir)
    services = await asyncio.to_thread(read_local_services, state_path, log_dir)
    semaphore = asyncio.Semaphore(4)
    # A configured HTTP proxy must not receive local runtime metadata.
    async with httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False) as client:
        async def probe(service):
            async with semaphore:
                if service["engine"] == "llama_cpp_python":
                    # A different backend with different metadata semantics;
                    # never treat it as the native executable or wake its model.
                    runtime = {"readiness": "unknown", "models": [], "loaded_models": [],
                               "context_length": None, "capabilities": {"modalities": None, "chat_template": None}}
                else:
                    runtime = await probe_local_runtime(client, service["engine"], service["base_url"])
            owned = service["process_ownership"] == "owned"
            from src.listener_attribution import listener_matches_process
            receipt = await asyncio.to_thread(controller._receipt, service['id']) if owned else None
            attributed = bool(receipt and receipt['pid'] == service.get('pid') and
                await asyncio.to_thread(listener_matches_process, service['base_url'], receipt['pid'], receipt['start_token']))
            # Passive attribution never changes the separate receipt-backed controls.
            return {**service, **runtime, "endpoint_ownership_verified": attributed,
                    "lifecycle_state": lifecycle_state(service["process_ownership"], service["task_status"], runtime["readiness"]),
                    "recovery": "observed" if owned else "not-owned",
                    "controls": {"start": False,
                                 "stop": await asyncio.to_thread(controller.control_permission, service["id"]),
                                 "restart": await asyncio.to_thread(controller.restart_permission, service["id"])}}
        return {"schema_version": 1, "services": await asyncio.gather(*(probe(s) for s in services))}
