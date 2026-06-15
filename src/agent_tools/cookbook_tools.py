"""
cookbook_tools.py — Cookbook tools for model download, serve, and management.

Handles: download_model, serve_model, list_served_models, stop_served_model,
tail_serve_output, list_downloads, cancel_download, search_hf_models,
list_cached_models, list_serve_presets, serve_preset, adopt_served_model,
and list_cookbook_servers.
"""

import asyncio
import json
import logging
import re
import shlex
from typing import Any, Dict, List, Optional

from core.constants import internal_api_base
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

# Helpers that stay in tool_implementations.py (shared with other tools there)
from src.tool_implementations import (
    _cookbook_servers,
    _resolve_cookbook_host,
    _cookbook_env_for_host,
    _ensure_served_endpoint,
    _cookbook_register_task,
    _cookbook_apply_retry_suggestion,
    _scan_running_model_processes,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal API helpers (mirrored from tool_implementations.py to avoid
# a circular dependency — keep in sync)
# ---------------------------------------------------------------------------

_INTERNAL_BASE = internal_api_base()


def _internal_headers(owner: Optional[str] = None) -> Dict[str, str]:
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner
    return headers


# ---------------------------------------------------------------------------
# Argument parsing (same pattern as document_tools.py)
# ---------------------------------------------------------------------------

def _parse_tool_args(content):
    """Parse a tool-call argument blob.

    Accepts either a JSON string or an already-decoded dict. Unwraps the
    common `{"body": {...}}` envelope that smaller models emit when they
    read tool descriptions like "Body is JSON: {...}" literally — they
    pass `body` as a field name rather than treating it as a noun.

    Returns a dict on success, raises ValueError on bad JSON.
    """
    if isinstance(content, str):
        try:
            args = json.loads(content) if content.strip() else {}
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(str(e))
    elif isinstance(content, dict):
        args = content
    else:
        args = {}
    # Unwrap {"body": {...}} envelope — but only if `body` is the sole key
    # and points at a dict. We don't want to clobber a legitimate `body`
    # field on tools where it's a real arg (e.g. send_email body text).
    if (
        isinstance(args, dict)
        and len(args) == 1
        and "body" in args
        and isinstance(args["body"], dict)
        and "action" in args["body"]  # extra safety: only unwrap if the inner dict looks like a tool call
    ):
        args = args["body"]
    return args


async def _cookbook_kill_session(session_id: str, *, remote_host: str = "",
                                 ssh_port: str = "", verb: str = "Stopped") -> Dict:
    """Kill a cookbook tmux session — remote-aware — AND mark the task
    stopped in cookbook_state.json. Shared by stop_served_model and
    cancel_download so both behave identically.

    Resolves the task's remote host from state when not passed in. A
    local-only `tmux kill-session` silently no-ops for remote tasks —
    that's the bug where "stop the download" appeared to work but the
    download kept running on the remote host.
    """
    import httpx
    headers = _internal_headers()
    remote = remote_host or ""
    sport = ssh_port or ""

    # Look up the task's host + confirm it exists in state.
    state: Dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = resp.json() or {}
    except Exception as e:
        logger.debug(f"cookbook state lookup failed for {session_id}: {e}")
    if not isinstance(state, dict):
        state = {}
    matched = None
    for t in (state.get("tasks") or []):
        if isinstance(t, dict) and (t.get("sessionId") == session_id or t.get("id") == session_id):
            matched = t
            if not remote:
                remote = t.get("remoteHost") or ""
            if not sport:
                sport = t.get("sshPort") or ""
            break

    if remote:
        _pf = f"-p {shlex.quote(str(sport))} " if sport and str(sport) != "22" else ""
        cmd = (
            f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
            f"{_pf}{shlex.quote(remote)} 'tmux kill-session -t {shlex.quote(session_id)}'"
        )
        target_label = f"{session_id} on {remote}"
    else:
        cmd = f"tmux kill-session -t {shlex.quote(session_id)}"
        target_label = session_id

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                     json={"command": cmd}, headers=headers)
        if resp.status_code >= 400:
            return {"error": f"shell/exec returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
        try:
            data = resp.json()
        except Exception:
            data = {}
        kill_failed = isinstance(data, dict) and data.get("exit_code") not in (None, 0)
        kill_err = ((data.get("stderr") or data.get("error") or "").strip() if isinstance(data, dict) else "")
        # "no server running" / "can't find session" means it was already
        # gone — treat as success (the goal is "not running").
        already_gone = any(s in kill_err.lower() for s in ("no server running", "can't find session", "session not found"))
        if kill_failed and not already_gone:
            return {"error": f"Failed to {verb.lower()} {target_label}: {kill_err or 'kill-session returned non-zero'}", "exit_code": 1}

        # Update state: mark stopped (so the UI + list reflect reality).
        if matched is not None:
            try:
                matched["status"] = "stopped"
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(f"{_INTERNAL_BASE}/api/cookbook/state",
                                      json=state, headers=headers)
            except Exception as e:
                logger.debug(f"failed to mark {session_id} stopped in state: {e}")

        suffix = " (was already gone)" if already_gone else ""
        return {"output": f"{verb} {target_label}{suffix}", "exit_code": 0}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Cookbook tool classes
# ---------------------------------------------------------------------------

class DownloadModelTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Download a HuggingFace model via the cookbook API."""
        import httpx
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        repo_id = args.get("repo_id", "")
        if not repo_id:
            return {"error": "repo_id is required", "exit_code": 1}
        host = (args.get("host") or "").strip()
        if host:
            host = await _resolve_cookbook_host(host)
        _host_defaulted = False
        if not host and not args.get("local"):
            _servers = await _cookbook_servers()
            if _servers.get("default_host"):
                host = _servers["default_host"]
                _host_defaulted = True
        backend = (args.get("backend") or "").strip().lower()
        if not backend and "/" not in repo_id and ":" in repo_id:
            backend = "ollama"
        payload = {"repo_id": repo_id}
        if backend:
            payload["backend"] = backend
        if host:
            payload["remote_host"] = host
        if args.get("include"):
            payload["include"] = args["include"]
        env_cfg = await _cookbook_env_for_host(host)
        if env_cfg.get("env_prefix"): payload["env_prefix"] = env_cfg["env_prefix"]
        if env_cfg.get("hf_token"):   payload["hf_token"]   = env_cfg["hf_token"]
        if env_cfg.get("platform"):   payload["platform"]   = env_cfg["platform"]
        if env_cfg.get("ssh_port"):   payload["ssh_port"]   = env_cfg["ssh_port"]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/model/download",
                                         json=payload, headers=_internal_headers())
                data = resp.json()
            if data.get("ok"):
                sid = data.get("session_id", "?")
                registered = await _cookbook_register_task(
                    session_id=sid, model=repo_id, host=host,
                    cmd=(f"ollama pull {repo_id}" if backend == "ollama" else f"hf download {repo_id}"),
                    task_type="download",
                )
                note = "" if registered else " (state-write failed — download may not show in UI)"
                where = host or "local"
                default_note = " (defaulted to the cookbook's selected server — pass host= or local=true to override)" if _host_defaulted else ""
                return {
                    "output": f"Download started: {repo_id} on {where} (session: {sid}){note}{default_note}",
                    "session_id": sid,
                    "host": host,
                    "task_type": "download",
                    "phase": "running",
                    "exit_code": 0,
                }
            return {"error": data.get("error", "Download failed"), "exit_code": 1}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class ServeModelTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Start serving a model via the cookbook API."""
        import httpx
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        repo_id = args.get("repo_id", "")
        cmd = args.get("cmd", "")
        if not repo_id or not cmd:
            return {"error": "repo_id and cmd are required", "exit_code": 1}
        host = (args.get("host") or "").strip()
        if host:
            host = await _resolve_cookbook_host(host)
        if not host and not args.get("local"):
            _servers = await _cookbook_servers()
            if _servers.get("default_host"):
                host = _servers["default_host"]
        payload = {"repo_id": repo_id, "cmd": cmd}
        if host:
            payload["remote_host"] = host
        env_cfg = await _cookbook_env_for_host(host)
        env_path = (env_cfg.get("env_path") or "").rstrip("/")
        env_type = (env_cfg.get("env_type") or env_cfg.get("env") or "").lower()
        if env_type == "venv" and env_path:
            venv_bin = f"{env_path}/bin"
            tokens = cmd.split()
            idx = 0
            env_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
            while idx < len(tokens) and env_re.match(tokens[idx]):
                idx += 1
            if idx < len(tokens):
                head = tokens[idx]
                if head in ("vllm", "python3", "python"):
                    tokens[idx] = f"{venv_bin}/{head}"
                    cmd = " ".join(tokens)
                    payload["cmd"] = cmd
        if env_cfg.get("env_prefix"): payload["env_prefix"] = env_cfg["env_prefix"]
        if env_cfg.get("gpus"):       payload["gpus"]       = env_cfg["gpus"]
        if env_cfg.get("hf_token"):   payload["hf_token"]   = env_cfg["hf_token"]
        if env_cfg.get("platform"):   payload["platform"]   = env_cfg["platform"]
        if env_cfg.get("ssh_port"):   payload["ssh_port"]   = env_cfg["ssh_port"]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/model/serve",
                                         json=payload, headers=_internal_headers())
                data = resp.json()
            if data.get("ok"):
                sid = data.get("session_id", "?")
                endpoint_id = data.get("endpoint_id") or ""
                if endpoint_id:
                    endpoint_added = True
                else:
                    endpoint_meta = await _ensure_served_endpoint(model=repo_id, cmd=cmd, host=host)
                    endpoint_added = bool(endpoint_meta.get("added"))
                    endpoint_id = endpoint_meta.get("endpoint_id", "") or endpoint_id
                registered = await _cookbook_register_task(
                    session_id=sid, model=repo_id,
                    host=host, cmd=cmd, task_type="serve",
                    endpoint_added=endpoint_added, endpoint_id=endpoint_id or "",
                )
                note = "" if registered else " (state-write failed — task may not show in UI)"
                return {
                    "output": f"Serving {repo_id} (session: {sid}){note}",
                    "session_id": sid,
                    "task_type": "serve",
                    "phase": "running",
                    "host": host,
                    "endpoint_id": endpoint_id,
                    "exit_code": 0,
                }
            err_msg = data.get("error") or data.get("detail") or "Serve failed"
            hint = ""
            if isinstance(err_msg, str) and "cmd" in err_msg.lower():
                hint = (" — the cmd must START with an allowlisted binary "
                        "(vllm, python3, llama-server, ollama, sglang, lmdeploy, node, npx). "
                        "Do NOT prefix with `cd …`, `source …`, or chain with `&&`. "
                        "env_prefix (e.g. `source ~/qwen35-env/bin/activate`) is added "
                        "automatically from the host's saved venv settings.")
            return {"error": f"{err_msg}{hint}", "exit_code": 1}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class ListServedModelsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """List running model servers — merges cookbook-tracked tasks with
        a /proc scan for externally-launched LLM/diffusion processes."""
        import httpx
        owner = ctx.get("owner")

        cookbook_tasks: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/tasks/status",
                                        headers=_internal_headers())
                cookbook_tasks = (resp.json() or {}).get("tasks") or []
        except Exception as e:
            logger.debug(f"cookbook tasks/status fetch failed: {e}")

        external = await asyncio.to_thread(_scan_running_model_processes)

        merged: List[Dict[str, Any]] = []
        merged.extend(cookbook_tasks)
        cookbook_pids = set()
        for t in cookbook_tasks:
            if isinstance(t, dict) and t.get("pid"):
                cookbook_pids.add(t["pid"])
        for p in external:
            if p.get("pid") not in cookbook_pids:
                merged.append(p)

        if not merged:
            return {
                "output": "No model servers currently running (cookbook task tracker empty; /proc scan found no vLLM / sglang / llama.cpp / Ollama / ComfyUI / A1111 / Fooocus / InvokeAI / TGI / Aphrodite / Triton / Diffusers processes).",
                "exit_code": 0,
            }

        _ORDER = {
            "ready": 0, "running": 1, "loading": 1, "warming": 1,
            "queued": 2, "starting": 2,
            "error": 5, "crashed": 5, "failed": 5,
            "stopped": 6, "killed": 6, "cancelled": 6, "canceled": 6,
            "done": 7, "completed": 7, "finished": 7,
        }
        def _rank(t: Dict[str, Any]) -> int:
            phase = (t.get("phase") or t.get("status") or "unknown").lower()
            return _ORDER.get(phase, 3)
        merged.sort(key=_rank)

        cb_n = len(cookbook_tasks)
        ext_n = len(external)
        live_n = sum(1 for t in merged if _rank(t) <= 2)
        header = []
        if cb_n:
            header.append(f"{cb_n} cookbook-tracked")
        if ext_n:
            header.append(f"{ext_n} external")
        if live_n:
            header.insert(0, f"{live_n} LIVE")
        lines = [f"Running: {', '.join(header)}."]
        for t in merged:
            phase = t.get("phase") or t.get("status", "unknown")
            model = t.get("model", "?")
            remote = t.get("remote", "local")
            sid = t.get("session_id", "?")
            tag = " [external]" if t.get("external") else ""
            lines.append(f"- {model}: {phase} ({remote}, session: {sid}){tag}")
            diag = t.get("diagnosis") if isinstance(t.get("diagnosis"), dict) else None
            if diag:
                lines.append(f"    diagnosis: {diag.get('message')}")
                cmd = t.get("cmd") or ""
                suggestions = diag.get("suggestions") or []
                actionable = []
                for s in suggestions[:3]:
                    label = s.get("label") or "retry"
                    retry_cmd = _cookbook_apply_retry_suggestion(cmd, s)
                    if retry_cmd and retry_cmd != cmd and s.get("op") in {"append", "replace", "remove"}:
                        actionable.append(f"{label}: `{retry_cmd}`")
                    else:
                        actionable.append(label)
                if actionable:
                    lines.append("    suggestions: " + " | ".join(actionable))
            if t.get("status") == "error" and t.get("output_tail"):
                tail = str(t.get("output_tail") or "").strip()
                if tail:
                    _tail_lines = tail.splitlines()
                    _shown = _tail_lines[-30:]
                    for _i, _ln in enumerate(_tail_lines):
                        if "Traceback (most recent call last)" in _ln or "ERROR" in _ln or "Error:" in _ln:
                            _shown = _tail_lines[_i:_i + 40]
                            break
                    lines.append("    recent log:")
                    for line in _shown:
                        lines.append(f"      {line[:220]}")
            if t.get("external") and t.get("cmdline_preview"):
                lines.append(f"    cmd: {t['cmdline_preview']}")
        return {"output": "\n".join(lines), "tasks": merged, "exit_code": 0}


class StopServedModelTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Stop a running model server by killing its tmux session (remote-aware)."""
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        session_id = args.get("session_id", "")
        if not session_id:
            return {"error": "session_id is required", "exit_code": 1}
        return await _cookbook_kill_session(
            session_id,
            remote_host=args.get("remote_host") or args.get("host") or "",
            ssh_port=args.get("ssh_port") or "",
            verb="Stopped server",
        )


class TailServeOutputTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Capture the last N lines of a cookbook task's tmux pane — remote-aware."""
        import httpx
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        session_id = (args.get("session_id") or "").strip()
        if not session_id:
            return {"error": "session_id is required (from list_served_models)", "exit_code": 1}
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
            return {"error": "Invalid session_id format", "exit_code": 1}
        try:
            tail = int(args.get("tail") or 400)
        except (TypeError, ValueError):
            tail = 400
        tail = max(20, min(tail, 4000))
        headers = _internal_headers()
        remote = (args.get("remote_host") or args.get("host") or "").strip()
        sport = (args.get("ssh_port") or "").strip()
        if not remote:
            state: Dict[str, Any] = {}
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                    state = resp.json() or {}
            except Exception as e:
                logger.debug(f"cookbook state lookup failed for {session_id}: {e}")
            if isinstance(state, dict):
                for t in (state.get("tasks") or []):
                    if isinstance(t, dict) and (t.get("sessionId") == session_id or t.get("id") == session_id):
                        remote = t.get("remoteHost") or ""
                        if not sport:
                            sport = t.get("sshPort") or ""
                        break
        log_path = f"/tmp/odysseus-tmux/{session_id}.log"
        pane_inner = f"tmux capture-pane -t {shlex.quote(session_id)} -p -S -{tail} 2>/dev/null"
        file_inner = f"tail -n {tail} {shlex.quote(log_path)} 2>/dev/null"
        inner = (
            f"if [ -s {shlex.quote(log_path)} ]; then {file_inner}; "
            f"else {pane_inner}; fi"
        )
        if remote:
            _pf = f"-p {shlex.quote(str(sport))} " if sport and str(sport) != "22" else ""
            cmd = (
                f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
                f"{_pf}{shlex.quote(remote)} {shlex.quote(inner)}"
            )
            host_label = remote
        else:
            cmd = inner
            host_label = "local"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                         json={"command": cmd}, headers=headers)
            if resp.status_code >= 400:
                return {"error": f"shell/exec returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
            data = resp.json() if resp.content else {}
            output_text = (data.get("stdout") or "").strip()
            stderr_text = (data.get("stderr") or "").strip()
            rc = data.get("exit_code")
            if rc not in (None, 0) and not output_text:
                already_gone = any(s in (stderr_text or "").lower() for s in ("no server running", "can't find session", "session not found"))
                if already_gone:
                    return {"output": f"Tmux session {session_id} on {host_label} is gone (task already exited).", "exit_code": 0, "session_id": session_id, "host": host_label}
                return {"error": f"capture-pane failed on {host_label}: {stderr_text or f'exit {rc}'}", "exit_code": 1}
            # Dedupe download-progress noise.
            dedup_lines = []
            seen_progress = set()
            progress_re = re.compile(r"^([\w./\-]+):\s+(\d+)%")
            for ln in output_text.splitlines():
                m = progress_re.match(ln.strip())
                if m:
                    key = (m.group(1), int(m.group(2)) // 10)
                    if key in seen_progress:
                        continue
                    seen_progress.add(key)
                dedup_lines.append(ln)
            output_text = "\n".join(dedup_lines)
            MAX_CHARS = 8000
            if len(output_text) > MAX_CHARS:
                output_text = "…(earlier output truncated)…\n" + output_text[-MAX_CHARS:]
            return {
                "output": output_text or "(empty pane)",
                "session_id": session_id,
                "host": host_label,
                "tail_lines": tail,
                "exit_code": 0,
            }
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class ListDownloadsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """List in-flight model downloads (filters /api/cookbook/tasks/status to type=download)."""
        import httpx
        owner = ctx.get("owner")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/tasks/status",
                                        headers=_internal_headers())
                data = resp.json()
            tasks = [t for t in data.get("tasks", []) if (t.get("type") or "").lower() == "download"]
            if not tasks:
                return {"output": "No downloads in progress.", "exit_code": 0}
            lines = [f"{len(tasks)} download(s) in progress:"]
            for t in tasks:
                phase = t.get("phase") or t.get("status", "unknown")
                model = t.get("model", "?")
                pct = t.get("progress_percent") or t.get("percent")
                pct_str = f" {pct}%" if pct is not None else ""
                lines.append(f"- {model}: {phase}{pct_str} ({t.get('remote', 'local')}, session: {t.get('session_id', '?')})")
            return {"output": "\n".join(lines), "downloads": tasks, "exit_code": 0}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class CancelDownloadTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Cancel a model download by killing its tmux session (remote-aware)."""
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        session_id = args.get("session_id", "")
        if not session_id:
            return {"error": "session_id is required (from list_downloads)", "exit_code": 1}
        return await _cookbook_kill_session(
            session_id,
            remote_host=args.get("remote_host") or args.get("host") or "",
            ssh_port=args.get("ssh_port") or "",
            verb="Cancelled download",
        )


class SearchHFModelsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Search HuggingFace via the cookbook /api/cookbook/hf-latest endpoint."""
        import httpx
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        query = args.get("query", "") or args.get("search", "")
        limit = args.get("limit", 10)
        params: Dict[str, str] = {}
        if query:
            params["search"] = query
        if limit:
            params["limit"] = str(limit)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/hf-latest",
                                        params=params, headers=_internal_headers())
                data = resp.json()
            models = data.get("models") if isinstance(data, dict) else data
            if not models:
                return {"output": f"No models found for query: {query!r}", "exit_code": 0}
            lines = [f"Found {len(models)} model(s) for {query!r}:" if query else f"{len(models)} model(s):"]
            for m in models[:limit if isinstance(limit, int) else 10]:
                if isinstance(m, dict):
                    name = m.get("repo_id") or m.get("modelId") or m.get("id") or "?"
                    dl = m.get("downloads")
                    size = m.get("size_gb") or m.get("needed_vram_gb")
                    bits = []
                    if size:
                        bits.append(f"~{size}GB")
                    if dl:
                        bits.append(f"{dl} downloads")
                    tail = f" ({', '.join(bits)})" if bits else ""
                    lines.append(f"- {name}{tail}")
                else:
                    lines.append(f"- {m}")
            return {"output": "\n".join(lines), "models": models, "exit_code": 0}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class AdoptServedModelTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Register an externally-launched model server into the Cookbook
        so it appears in list_served_models and can be stopped."""
        import httpx
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        host = (args.get("host") or args.get("remote_host") or "").strip()
        sess = (args.get("tmux_session") or args.get("session_id") or "").strip()
        model = (args.get("model") or args.get("repo_id") or "").strip()
        port = args.get("port") or 8000
        display_name = (args.get("name") or "").strip() or (model.split("/")[-1] if "/" in model else model)
        add_endpoint = args.get("add_endpoint", True)

        if not sess or not model:
            return {"error": "tmux_session and model are required", "exit_code": 1}

        headers = _internal_headers()
        if host:
            check = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {shlex.quote(host)} 'tmux has-session -t {shlex.quote(sess)} 2>&1'"
        else:
            check = f"tmux has-session -t {shlex.quote(sess)} 2>&1"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                      json={"command": check}, headers=headers)
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code >= 400 or (data.get("exit_code") not in (None, 0)):
                err = (data.get("stderr") or data.get("error") or r.text[:200]).strip()
                return {"error": f"tmux session {sess!r} not found on {host or 'local'}: {err}", "exit_code": 1}
        except Exception as e:
            return {"error": f"verify failed: {e}", "exit_code": 1}

        if host:
            health_cmd = f"ssh -o ConnectTimeout=5 {shlex.quote(host)} 'curl -s -m 3 http://localhost:{int(port)}/v1/models'"
        else:
            health_cmd = f"curl -s -m 3 http://localhost:{int(port)}/v1/models"
        server_up = False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                      json={"command": health_cmd}, headers=headers)
                body = (r.json() or {}).get("stdout", "") if r.headers.get("content-type", "").startswith("application/json") else ""
                server_up = '"data"' in body or '"object"' in body
        except Exception:
            pass

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as e:
            return {"error": f"could not read cookbook state: {e}", "exit_code": 1}
        if not isinstance(state, dict):
            state = {}
        tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
        if any(isinstance(t, dict) and t.get("sessionId") == sess for t in tasks):
            adopted_already = True
        else:
            adopted_already = False
            import time as _time
            new_task = {
                "id": sess,
                "sessionId": sess,
                "name": display_name,
                "type": "serve",
                "status": "running",
                "output": (
                    f"Adopted externally-launched session {sess!r} on {host or 'local'}.\n"
                    "Reconnect polling will start streaming tmux output shortly."
                ),
                "ts": int(_time.time() * 1000),
                "payload": {"repo_id": model, "remote_host": host or "", "_cmd": "(adopted — launched outside cookbook)"},
                "remoteHost": host or "",
                "sshPort": "",
                "platform": "linux",
                "_serveReady": bool(server_up),
                "_endpointAdded": False,
                "_adoptedExternally": True,
            }
            tasks.append(new_task)
            state["tasks"] = tasks
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(f"{_INTERNAL_BASE}/api/cookbook/state",
                                      json=state, headers=headers)
            except Exception as e:
                return {"error": f"could not save cookbook state: {e}", "exit_code": 1}

        endpoint_msg = ""
        if add_endpoint:
            host_only = host.split("@", 1)[-1] if host else "localhost"
            endpoint_url = f"http://{host_only}:{int(port)}/v1"
            try:
                from src.tool_implementations import do_manage_endpoints
            except Exception:
                do_manage_endpoints = None
            if do_manage_endpoints is not None:
                try:
                    ep_result = await do_manage_endpoints(json.dumps({
                        "action": "add",
                        "name": display_name,
                        "endpoint_url": endpoint_url,
                        "is_local": False,
                    }), owner=owner)
                    if isinstance(ep_result, dict) and not ep_result.get("error"):
                        endpoint_msg = f" Endpoint {endpoint_url} added as {display_name!r}."
                    else:
                        endpoint_msg = f" Endpoint registration skipped: {(ep_result or {}).get('error', 'unknown')}"
                except Exception as e:
                    endpoint_msg = f" Endpoint registration failed: {e}"

        return {
            "output": (
                f"Adopted session {sess!r} ({model}) on {host or 'local'}:{port}. "
                + ("Already tracked — skipped state write. " if adopted_already else "Added to cookbook state. ")
                + ("Server responding. " if server_up else "Server not responding yet (still loading?). ")
                + endpoint_msg
            ).strip(),
            "session_id": sess,
            "host": host,
            "port": int(port),
            "server_up": server_up,
            "exit_code": 0,
        }


class ListCookbookServersTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """List the cookbook's configured servers and the current default host."""
        owner = ctx.get("owner")
        servers = await _cookbook_servers()
        hosts = servers.get("hosts") or []
        default = servers.get("default_host") or ""
        if not hosts:
            return {"output": "No cookbook servers configured. Downloads/serves default to localhost.", "servers": [], "default_host": "", "exit_code": 0}
        default_name = next((h.get("name") for h in hosts if h.get("host") == default and h.get("name")), default or "local")
        lines = [f"{len(hosts)} configured server(s) (default: {default_name}):"]
        for h in hosts:
            name = h.get("name") or "(unnamed)"
            host = h.get("host") or "local"
            mark = " ← default" if h.get("host") == default else ""
            env_bit = f" [{h.get('env')}: {h.get('envPath')}]" if h.get("env") and h.get("env") != "none" else ""
            plat = f" ({h.get('platform')})" if h.get("platform") else ""
            lines.append(f"- {name} → {host}{plat}{env_bit}{mark}")
        lines.append("\nRefer to servers by their name (e.g. download_model with host=\"gpu-box\").")
        return {"output": "\n".join(lines), "servers": hosts, "default_host": default, "exit_code": 0}


class ListServePresetsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """List saved serve presets from cookbook_state.json."""
        import httpx
        owner = ctx.get("owner")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state",
                                        headers=_internal_headers())
                state = resp.json() or {}
        except Exception as e:
            return {"error": f"Failed to fetch cookbook state: {e}", "exit_code": 1}

        presets = state.get("presets") or []
        if not presets:
            return {
                "output": "No serve presets saved. Tell the user to save one from the Cookbook UI first, or use serve_model with explicit repo_id + cmd + host.",
                "presets": [],
                "exit_code": 0,
            }
        lines = [f"{len(presets)} saved serve preset(s):"]
        for p in presets:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "?")
            model = p.get("model") or p.get("modelId") or "?"
            host = p.get("host") or p.get("remoteHost") or "local"
            port = p.get("port", "")
            cmd = (p.get("cmd") or "").strip()
            bits = [f"- {name}: {model}", f"host={host}"]
            if port:
                bits.append(f"port={port}")
            lines.append("  ".join(bits))
            if cmd:
                cmd_preview = cmd if len(cmd) < 140 else cmd[:140] + "…"
                lines.append(f"    cmd: {cmd_preview}")
        return {"output": "\n".join(lines), "presets": presets, "exit_code": 0}


class ServePresetTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Launch a saved serve preset by name."""
        import httpx
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        name = (args.get("name") or args.get("preset") or "").strip()
        if not name:
            return {"error": "name (preset name) is required. Call list_serve_presets to see what's available.", "exit_code": 1}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state",
                                        headers=_internal_headers())
                state = resp.json() or {}
        except Exception as e:
            return {"error": f"Failed to fetch cookbook state: {e}", "exit_code": 1}

        presets = state.get("presets") or []
        chosen = None
        lname = name.lower()
        for p in presets:
            if isinstance(p, dict) and (p.get("name") or "").lower() == lname:
                chosen = p
                break
        if chosen is None:
            for p in presets:
                if isinstance(p, dict) and lname in (p.get("name") or "").lower():
                    chosen = p
                    break
        if chosen is None:
            sample = ", ".join((p.get("name") or "?") for p in presets[:8] if isinstance(p, dict))
            return {"error": f"No preset matching {name!r}. Available: {sample or '(none)'}", "exit_code": 1}

        repo_id = chosen.get("model") or chosen.get("modelId") or ""
        cmd = (chosen.get("cmd") or "").strip()
        host = chosen.get("host") or chosen.get("remoteHost") or ""
        if not repo_id or not cmd:
            return {"error": f"Preset {chosen.get('name')!r} is missing model or cmd — can't launch.", "exit_code": 1}

        payload: Dict[str, Any] = {"repo_id": repo_id, "cmd": cmd}
        if host:
            payload["remote_host"] = host
        env_cfg = await _cookbook_env_for_host(host)
        if env_cfg.get("env_prefix"): payload["env_prefix"] = env_cfg["env_prefix"]
        if env_cfg.get("gpus"):       payload["gpus"]       = env_cfg["gpus"]
        if env_cfg.get("hf_token"):   payload["hf_token"]   = env_cfg["hf_token"]
        if env_cfg.get("platform"):   payload["platform"]   = env_cfg["platform"]
        if env_cfg.get("ssh_port"):
            payload["ssh_port"] = env_cfg["ssh_port"]

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/model/serve",
                                         json=payload, headers=_internal_headers())
                data = resp.json()
            if data.get("ok"):
                sid = data.get("session_id", "?")
                endpoint_id = data.get("endpoint_id") or ""
                if endpoint_id:
                    endpoint_added = True
                else:
                    endpoint_meta = await _ensure_served_endpoint(model=repo_id, cmd=cmd, host=host)
                    endpoint_added = bool(endpoint_meta.get("added"))
                    endpoint_id = endpoint_meta.get("endpoint_id", "") or endpoint_id
                registered = await _cookbook_register_task(
                    session_id=sid, model=repo_id, host=host,
                    cmd=cmd, task_type="serve",
                    endpoint_added=endpoint_added, endpoint_id=endpoint_id or "",
                )
                note = "" if registered else " (state-write failed — task may not show in UI)"
                return {"output": f"Launched preset {chosen.get('name')!r}: {repo_id} on {host or 'local'} (session: {sid}){note}", "session_id": sid, "host": host, "endpoint_id": endpoint_id, "exit_code": 0}
            return {"error": data.get("error", "Serve failed"), "exit_code": 1}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class ListCachedModelsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """List models already cached locally and/or on remote hosts."""
        import httpx
        from collections import defaultdict as _dd
        owner = ctx.get("owner")
        try:
            args = _parse_tool_args(content) if content.strip() else {}
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        raw_host = (args.get("host") or "").strip()
        headers = _internal_headers()

        async def _scan_one(host_label: str, host_val: str, ssh_port: str = "",
                            platform: str = "", model_dir: str = "") -> list:
            p: Dict[str, str] = {}
            if host_val:
                p["host"] = host_val
            if args.get("model_dir"):
                p["model_dir"] = args["model_dir"]
            elif model_dir:
                p["model_dir"] = model_dir
            if ssh_port:
                p["ssh_port"] = ssh_port
            elif args.get("ssh_port"):
                p["ssh_port"] = str(args["ssh_port"])
            if platform:
                p["platform"] = platform
            elif args.get("platform"):
                p["platform"] = args["platform"]
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(f"{_INTERNAL_BASE}/api/model/cached",
                                            params=p, headers=headers)
                    data = resp.json()
                ms = data.get("models", []) if isinstance(data, dict) else (data or [])
                for m in ms:
                    m["host"] = host_label or "local"
                return ms or []
            except Exception as e:
                logger.debug(f"list_cached_models scan({host_label}) failed: {e}")
                return []

        try:
            servers: list = []
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    st = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                    st_data = st.json() if st.headers.get("content-type", "").startswith("application/json") else {}
                servers = (st_data.get("env", {}) or {}).get("servers") or []
            except Exception as e:
                logger.debug(f"server list fetch failed: {e}")

            def _dirs_for(server_record: Dict[str, Any]) -> str:
                mds = server_record.get("modelDirs") if isinstance(server_record, dict) else None
                HF_DEFAULTS = {"~/.cache/huggingface/hub", "~/.cache/huggingface"}
                if isinstance(mds, list):
                    extras = [d for d in mds if isinstance(d, str) and d.strip() and d.strip() not in HF_DEFAULTS]
                    return ",".join(extras)
                if isinstance(mds, str) and mds.strip() not in HF_DEFAULTS:
                    return mds
                return ""

            if raw_host:
                host = await _resolve_cookbook_host(raw_host)
                srv = next(
                    (s for s in servers if isinstance(s, dict)
                     and (s.get("name") == raw_host or s.get("host") == host or s.get("host") == raw_host)),
                    {},
                )
                models = await _scan_one(raw_host, host, model_dir=_dirs_for(srv))
            else:
                local_srv = next((s for s in servers if isinstance(s, dict) and not (s.get("host") or "").strip()), {})
                scans: list = [_scan_one("local", "", model_dir=_dirs_for(local_srv))]
                for s in servers:
                    if not isinstance(s, dict):
                        continue
                    name = s.get("name") or s.get("host")
                    host_val = s.get("host") or ""
                    if not host_val:
                        continue
                    scans.append(_scan_one(
                        name, host_val,
                        ssh_port=str(s.get("port") or ""),
                        platform=s.get("platform") or "",
                        model_dir=_dirs_for(s),
                    ))
                results = await asyncio.gather(*scans, return_exceptions=False)
                seen = set()
                models: list = []
                for batch in results:
                    for m in batch:
                        key = (m.get("host", ""), m.get("repo_id", ""))
                        if key in seen:
                            continue
                        seen.add(key)
                        models.append(m)
            if not models:
                downloaded = []
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        st = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                        state = st.json() if st.headers.get("content-type", "").startswith("application/json") else {}
                    for t in (state.get("tasks") or []):
                        if not isinstance(t, dict) or t.get("type") != "download":
                            continue
                        if (t.get("status") or "").lower() not in {"done", "completed"}:
                            continue
                        task_host = t.get("remoteHost") or (t.get("payload") or {}).get("remote_host") or ""
                        if raw_host and task_host != raw_host:
                            continue
                        repo = t.get("modelId") or t.get("repoId") or (t.get("payload") or {}).get("repo_id") or t.get("name")
                        if repo and repo not in downloaded:
                            downloaded.append(repo)
                except Exception:
                    downloaded = []
                host_str = f" on {raw_host}" if raw_host else ""
                if downloaded:
                    lines = [f"No cache paths were detected{host_str}, but Cookbook has completed download task(s):"]
                    lines.extend(f"- {repo} — downloaded via Cookbook task" for repo in downloaded)
                    return {"output": "\n".join(lines), "models": [{"repo_id": repo, "source": "cookbook_task"} for repo in downloaded], "exit_code": 0}
                return {"output": f"No cached models found{host_str}.", "exit_code": 0}
            if raw_host:
                lines = [f"{len(models)} cached model(s) on {raw_host}:"]
                for m in models:
                    name = m.get("repo_id", "?")
                    sz = m.get("size") or (f"{m.get('size_bytes', 0) / (1024**3):.1f}GB" if m.get("size_bytes") else "")
                    inc = " (incomplete)" if m.get("has_incomplete") else ""
                    kind = " [diffusion]" if m.get("is_diffusion") else ""
                    lines.append(f"- {name}{kind} — {sz}{inc}")
            else:
                by_host = _dd(list)
                for m in models:
                    by_host[m.get("host", "local")].append(m)
                lines = [f"{len(models)} cached model(s) across {len(by_host)} server(s):"]
                for host_name in sorted(by_host.keys()):
                    lines.append(f"\n[{host_name}]")
                    for m in by_host[host_name]:
                        name = m.get("repo_id", "?")
                        sz = m.get("size") or (f"{m.get('size_bytes', 0) / (1024**3):.1f}GB" if m.get("size_bytes") else "")
                        inc = " (incomplete)" if m.get("has_incomplete") else ""
                        kind = " [diffusion]" if m.get("is_diffusion") else ""
                        backend = f" ({m.get('backend')})" if m.get("backend") else ""
                        lines.append(f"- {name}{kind}{backend} — {sz}{inc}")
            return {"output": "\n".join(lines), "models": models, "exit_code": 0}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}
