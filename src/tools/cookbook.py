"""Cookbook (model serving) tool domain — slice 1 (#4082/#4071).

Download, serve, list, stop, tail, search, adopt and cache HuggingFace / model
serving operations, plus their private helpers. Extracted verbatim from
``src.tool_implementations.py``; this module is re-exported by the facade.

Shared constants ``_internal_headers`` and ``_INTERNAL_BASE`` still live in
``src.tool_implementations`` (used by many domains); each function that needs
them does a function-local import to avoid a top-level circular dependency,
matching the system-domain split.
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from routes._validators import validate_remote_host, validate_ssh_port

from src.tools._common import _parse_tool_args

logger = logging.getLogger(__name__)


def _string_arg(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _validate_cookbook_ssh_target(remote_host: Any, ssh_port: Any = "") -> tuple[str, str]:
    remote = validate_remote_host(_string_arg(remote_host) or None) or ""
    sport = validate_ssh_port(_string_arg(ssh_port) or None) or ""
    return remote, sport


async def _cookbook_servers() -> Dict[str, Any]:
    """Return the cookbook's configured servers + the currently-selected
    default host. Shape: {default_host, hosts: [{host, platform, env, envPath}]}.
    The agent uses this to route downloads/serves to the right machine
    instead of silently defaulting to localhost."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=_internal_headers())
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception:
        return {"default_host": "", "hosts": []}
    env = (state or {}).get("env") or {}
    if not isinstance(env, dict):
        return {"default_host": "", "hosts": []}
    hosts = []
    for s in (env.get("servers") or []):
        if isinstance(s, dict):
            hosts.append({
                "name": s.get("name") or "",
                "host": s.get("host") or "",   # "" = Local
                "platform": s.get("platform") or "",
                "env": s.get("env") or "",
                "envPath": s.get("envPath") or "",
                "port": s.get("port") or "",
            })
    return {"default_host": env.get("remoteHost") or "", "hosts": hosts}


async def _resolve_cookbook_host(name_or_host: str) -> str:
    """Map a friendly server NAME ('gpu-box', 'workstation') to its ssh host
    string ('user@192.0.2.10'). If the input already looks like an
    ssh host (contains '@' or matches a known host), or matches nothing,
    it's returned unchanged. 'local'/'localhost' → '' (this machine)."""
    if not name_or_host:
        return ""
    val = name_or_host.strip()
    low = val.lower()
    if low in ("local", "localhost", "this machine", "here"):
        return ""
    servers = await _cookbook_servers()
    # Exact host match → already an ssh host
    for h in servers.get("hosts") or []:
        if h.get("host") and h["host"] == val:
            return val
    # Name match (case-insensitive)
    for h in servers.get("hosts") or []:
        if (h.get("name") or "").lower() == low:
            return h.get("host") or ""   # "" for the Local entry
    # Substring name match as a fallback
    for h in servers.get("hosts") or []:
        if low and low in (h.get("name") or "").lower():
            return h.get("host") or ""
    # No match — assume the caller passed a raw host/alias; return as-is
    # (ssh can resolve aliases from ~/.ssh/config).
    return val


async def _cookbook_env_for_host(host: str) -> Dict[str, Any]:
    """Resolve env_prefix / gpus / platform / hf_token / ssh_port for a
    given host by looking it up in cookbook_state.env. The user
    configures these per-host in the Cookbook UI; without them, raw
    `vllm serve …` fails with 'command not found' because vLLM lives
    inside a venv that has to be sourced first.

    Returns a dict with keys ready to drop into the /api/model/serve
    payload: env_prefix, gpus, platform, hf_token, ssh_port.
    Falls back to the top-level env settings if no per-host entry exists.
    """
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    headers = _internal_headers()
    state: Dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        logger.debug(f"cookbook env lookup failed for host={host!r}: {e}")
        return {}
    if not isinstance(state, dict):
        return {}
    env_root = state.get("env") or {}
    if not isinstance(env_root, dict):
        return {}

    # Per-host entry takes precedence over top-level.
    per_host: Dict[str, Any] = {}
    for s in (env_root.get("servers") or []):
        if isinstance(s, dict) and (s.get("host") or "") == (host or ""):
            per_host = s
            break

    env_kind = per_host.get("env") or env_root.get("env") or "none"
    env_path = per_host.get("envPath") or env_root.get("envPath") or ""
    platform = per_host.get("platform") or env_root.get("platform") or "linux"
    ssh_port = per_host.get("sshPort") or env_root.get("sshPort") or ""

    env_prefix = ""
    if env_kind == "venv" and env_path:
        if platform == "windows":
            activate = env_path if env_path.endswith("\\Scripts\\Activate.ps1") else env_path.rstrip("\\") + "\\Scripts\\Activate.ps1"
            env_prefix = f"& {activate}"
        else:
            activate = env_path if env_path.endswith("/bin/activate") else env_path.rstrip("/") + "/bin/activate"
            env_prefix = f"source {activate}"
    elif env_kind == "conda" and env_path:
        if platform == "windows":
            env_prefix = f"conda activate {env_path}"
        else:
            env_prefix = f'eval "$(conda shell.bash hook)" && conda activate {env_path}'

    from routes.cookbook_helpers import load_stored_hf_token
    return {
        "env_prefix": env_prefix,
        "env_type": env_kind,
        "env_path": env_path,
        "gpus": env_root.get("gpus") or "",
        "platform": platform,
        "hf_token": load_stored_hf_token(),
        "ssh_port": ssh_port,
    }


def _infer_serve_port(cmd: str) -> int:
    """Infer likely listen port from a serve command."""
    if not cmd:
        return 8080
    m = re.search(r"--port\\s+(\\d+)", cmd)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    m = re.search(r"OLLAMA_HOST=[^\\s]*?:(\\d+)", cmd)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    if "ollama" in cmd:
        return 11434
    return 8080


def _infer_serve_host(host: str | None) -> tuple[str, bool]:
    """Return (host, container_local) for registering a served endpoint."""
    if not (host or "").strip():
        return "localhost", True
    base_host = host.split("@", 1)[-1] if "@" in host else host
    return base_host, False


async def _ensure_served_endpoint(
    *,
    model: str,
    cmd: str,
    host: str | None,
) -> Dict[str, Any]:
    """Register/fetch a model endpoint for a running serve session."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    endpoint_host, container_local = _infer_serve_host(host)
    port = _infer_serve_port(cmd)
    base_url = f"http://{endpoint_host}:{port}/v1"
    short_name = model.split("/")[-1] if "/" in model else model
    is_image = "diffusion_server.py" in (cmd or "")
    payload = {
        "name": short_name if not is_image else f"{short_name} (image)",
        "base_url": base_url,
        "skip_probe": "true",
        "model_type": "image" if is_image else "llm",
        "container_local": "true" if container_local else "false",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_INTERNAL_BASE}/api/model-endpoints",
                data=payload,
                headers=_internal_headers(),
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code >= 400:
            logger.debug(
                f"ensure endpoint failed for {model!r}: status={resp.status_code} data={data}"
            )
            return {"added": False, "endpoint_id": "", "base_url": base_url, "error": data}
        ep_id = data.get("id") if isinstance(data, dict) else None
        return {
            "added": bool(ep_id),
            "endpoint_id": ep_id or "",
            "base_url": base_url,
            "data": data,
        }
    except Exception as e:
        logger.debug(f"ensure endpoint exception for {model!r}: {e}")
        return {"added": False, "endpoint_id": "", "base_url": base_url, "error": str(e)}


async def _cookbook_register_task(
    session_id: str,
    model: str,
    host: str,
    cmd: str,
    task_type: str = "serve",
    *,
    endpoint_added: bool = False,
    endpoint_id: str = "",
) -> bool:
    """Append a task entry to cookbook_state.json after the agent
    launches via /api/model/serve or /api/model/download. The route
    spawns tmux but leaves state-writing to the UI; the agent needs to
    do that here so the task shows up in the Cookbook tab.
    Returns True on success, False if the write failed (best-effort)."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    import time as _time
    headers = _internal_headers()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        logger.debug(f"cookbook state read failed: {e}")
        return False
    if not isinstance(state, dict):
        state = {}
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    # Skip duplicate (same session_id) entries
    if any(isinstance(t, dict) and t.get("sessionId") == session_id for t in tasks):
        return True
    display_name = model.split("/")[-1] if "/" in model else model
    # Placeholder output — the cookbook UI's CSS hides empty <pre>
    # via `.cookbook-output-pre:empty { display: none }`, so an
    # empty-string output makes the expansion appear broken until the
    # frontend's reconnect-polling loop captures tmux output. A short
    # placeholder gives the user something to see immediately; it gets
    # replaced by real tmux output within a few seconds.
    target = f"{host}:" if host else "local:"
    placeholder = (
        f"Launched via agent — waiting for tmux output…\n"
        f"  session: {session_id}\n"
        f"  target:  {target}{(cmd.split() or [''])[0] if cmd else ''}\n"
        f"  cmd:     {cmd[:200]}{'…' if len(cmd) > 200 else ''}"
    )
    tasks.append({
        "id": session_id,
        "sessionId": session_id,
        "name": display_name,
        "modelId": model,
        "type": task_type,
        "status": "running",
        "output": placeholder,
        "ts": int(_time.time() * 1000),
        "payload": {"repo_id": model, "remote_host": host or "", "_cmd": cmd},
        "remoteHost": host or "",
        "sshPort": "",
        "platform": "linux",
        "_serveReady": False,
        "_endpointAdded": bool(endpoint_added),
        "_endpointId": endpoint_id or "",
    })
    state["tasks"] = tasks
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{_INTERNAL_BASE}/api/cookbook/state",
                                  json=state, headers=headers)
        return r.status_code < 400
    except Exception as e:
        logger.debug(f"cookbook state write failed: {e}")
        return False




# Patterns for detecting running LLM/diffusion model servers outside
# the cookbook's task tracker. Each entry: (label, substring-list).
# Match is case-insensitive against the FULL cmdline. First-match wins.
_MODEL_PROCESS_PATTERNS = [
    ("vLLM",            ["vllm.entrypoints", "vllm serve", "/vllm/", "vllm-openai"]),
    ("SGLang",          ["sglang.launch_server", "sglang/launch_server"]),
    ("MLX",             ["mlx_lm.server", "mlx-lm"]),
    ("llama.cpp",       ["llama-server", "llama_cpp_server", "llamacppserver"]),
    ("Ollama",          ["ollama serve", "ollama runner", "/ollama "]),
    ("ComfyUI",         ["comfyui/main.py", "/ComfyUI/main.py", "ComfyUI"]),
    ("A1111 WebUI",     ["stable-diffusion-webui/webui", "stable-diffusion-webui/launch", "webui.sh"]),
    ("Fooocus",         ["Fooocus/entry_with_update", "Fooocus/launch"]),
    ("InvokeAI",        ["invokeai-web", "invokeai.app", "invokeai/api_app"]),
    ("Forge WebUI",     ["stable-diffusion-webui-forge", "forge/webui"]),
    ("SD.Next",         ["automatic/webui", "sd.next"]),
    ("TGI",             ["text-generation-launcher", "text_generation_launcher"]),
    ("Aphrodite",       ["aphrodite.endpoints", "aphrodite-engine"]),
    ("Triton",          ["tritonserver", "triton/main"]),
    ("Diffusers",       ["diffusers.pipelines", "StableDiffusionInpaintPipeline", "DiffusionPipeline"]),
]


def _cookbook_apply_retry_suggestion(cmd: str, suggestion: Dict[str, Any]) -> str:
    """Apply a structured Cookbook diagnosis suggestion to a serve command."""
    if not cmd or not suggestion:
        return cmd
    op = suggestion.get("op")
    if op == "append":
        arg = (suggestion.get("arg") or "").strip()
        if not arg or arg in cmd:
            return cmd
        return f"{cmd.rstrip()} {arg}"
    if op == "remove":
        flag = (suggestion.get("flag") or "").strip()
        if not flag:
            return cmd
        return re.sub(rf"\s*{re.escape(flag)}(?:\s+\S+)?", "", cmd).strip()
    if op == "replace":
        flag = (suggestion.get("flag") or "").strip()
        value = str(suggestion.get("value") or "").strip()
        if not flag or not value:
            return cmd
        repl = f"{flag} {value}"
        if re.search(rf"(^|\s){re.escape(flag)}(\s+\S+)?", cmd):
            return re.sub(rf"(^|\s){re.escape(flag)}(?:\s+\S+)?", lambda m: (m.group(1) or " ") + repl, cmd).strip()
        return f"{cmd.rstrip()} {repl}"
    return cmd


def _scan_running_model_processes() -> List[Dict[str, Any]]:
    """Scan /proc for running model server processes. Linux-only; returns
    [] on other platforms or if /proc isn't accessible. Each match returns
    a dict shaped like a cookbook task so the caller can merge cleanly.
    """
    import os
    if not os.path.isdir("/proc"):
        return []
    out: List[Dict[str, Any]] = []
    seen_keys = set()
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    raw = f.read()
            except (OSError, PermissionError):
                continue
            if not raw:
                continue
            # cmdline is NUL-separated; join with spaces for matching/display
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            if not cmdline:
                continue
            lower = cmdline.lower()
            for label, needles in _MODEL_PROCESS_PATTERNS:
                if any(n.lower() in lower for n in needles):
                    # Dedupe by (label, first-arg) — multi-worker servers
                    # spawn N processes; only show one row per server.
                    key = (label, cmdline.split(" ")[0])
                    if key in seen_keys:
                        break
                    seen_keys.add(key)
                    # Try to pluck a model name out of the cmdline.
                    model = ""
                    for tok in cmdline.split():
                        if "/" in tok and any(s in tok.lower() for s in (
                            "model", "checkpoint", ".safetensors", ".gguf", ".bin", "huggingface"
                        )):
                            model = tok
                            break
                    out.append({
                        "session_id": f"pid-{pid_dir}",
                        "model": model or label,
                        "phase": "running (external)",
                        "type": "serve",
                        "remote": "local",
                        "pid": int(pid_dir),
                        "label": label,
                        "cmdline_preview": cmdline[:140] + ("…" if len(cmdline) > 140 else ""),
                        "external": True,
                    })
                    break
    except Exception as e:
        logger.debug(f"_scan_running_model_processes failed: {e}")
    return out


async def do_download_model(content: str, owner: Optional[str] = None) -> Dict:
    """Download a HuggingFace model via the cookbook API."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    repo_id = args.get("repo_id", "")
    if not repo_id:
        return {"error": "repo_id is required", "exit_code": 1}
    host = (args.get("host") or "").strip()
    # Resolve a friendly server NAME ("gpu-box") to its ssh host string.
    if host:
        host = await _resolve_cookbook_host(host)
    # No host specified → default to the cookbook's currently-selected
    # server rather than silently downloading to localhost (which is
    # usually NOT where the GPUs / model cache live).
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
    # Per-host env_prefix + hf_token from cookbook_state (same as serve).
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


async def do_serve_model(content: str, owner: Optional[str] = None) -> Dict:
    """Start serving a model via the cookbook API."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
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
    # Resolve per-host env settings (venv/conda activate, gpus,
    # hf_token, platform, ssh_port) from cookbook_state — same path
    # the UI uses. Without env_prefix, `vllm serve …` lands in a shell
    # without the user's venv and fails 'command not found'.
    env_cfg = await _cookbook_env_for_host(host)
    # Rewrite bare `vllm` / `python3` leading tokens to the venv's absolute
    # binary path when the target host has a venv configured. SSH non-
    # interactive shells often leave ~/.local/bin ahead of the venv bin on
    # PATH even with the venv activated, so `vllm serve` finds the wrong
    # binary and crashes early (e.g. compute_89 torch ABI errors on an old
    # user-site torch). This mirrors what static/js/cookbook.js does in
    # _buildServeCmd for the UI launch path.
    env_path = (env_cfg.get("env_path") or "").rstrip("/")
    env_type = (env_cfg.get("env_type") or env_cfg.get("env") or "").lower()
    if env_type == "venv" and env_path:
        venv_bin = f"{env_path}/bin"
        # Match the FIRST shell-token: skip leading KEY=VAL env-var prefixes
        # (CUDA_VISIBLE_DEVICES=… VLLM_USE_FLASHINFER_SAMPLER=…) before the binary.
        import re as _re3
        tokens = cmd.split()
        idx = 0
        env_re = _re3.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
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
            where = host or "local"
            log_path = f"/tmp/odysseus-tmux/{sid}.log"
            return {
                "output": (
                    f"Serving {repo_id} on {where} (session: {sid}){note}\n"
                    f"Next required check: call list_served_models. If this task is not ready, "
                    f"call tail_serve_output with session_id={sid} and tail=400 before answering. "
                    f"Do not tell the user to check logs; you have the log tool."
                ),
                "session_id": sid,
                "task_type": "serve",
                "phase": "running",
                "host": host,
                "endpoint_id": endpoint_id,
                "log_path": log_path,
                "next_tools": [
                    {"name": "list_served_models", "arguments": {}},
                    {"name": "tail_serve_output", "arguments": {"session_id": sid, "tail": 400}},
                ],
                "exit_code": 0,
            }
        # FastAPI HTTPException puts the message under `detail`, not `error`.
        # Surface BOTH so the agent sees "Invalid characters in cmd" (from
        # _validate_serve_cmd rejecting `&&`/`source`/`cd`) instead of
        # the generic "Serve failed", which leaves it with nothing to act on.
        err_msg = data.get("error") or data.get("detail") or "Serve failed"
        hint = ""
        if isinstance(err_msg, str) and "cmd" in err_msg.lower():
            hint = (" — the cmd must START with an allowlisted binary "
                    "(vllm, python3, llama-server, ollama, sglang, mlx_lm, lmdeploy, node, npx). "
                    "Do NOT prefix with `cd …`, `source …`, or chain with `&&`. "
                    "env_prefix (e.g. `source ~/qwen35-env/bin/activate`) is added "
                    "automatically from the host's saved venv settings.")
        return {"error": f"{err_msg}{hint}", "exit_code": 1}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_list_served_models(content: str, owner: Optional[str] = None) -> Dict:
    """List running model servers — merges cookbook-tracked tasks with
    a /proc scan for externally-launched LLM/diffusion processes
    (vLLM, sglang, llama.cpp, Ollama, ComfyUI, A1111, Fooocus, etc.)."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import asyncio
    import httpx

    # Cookbook-tracked tasks (best-effort; don't fail the whole call if
    # this is unreachable).
    cookbook_tasks: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/tasks/status",
                                    headers=_internal_headers())
            cookbook_tasks = (resp.json() or {}).get("tasks") or []
    except Exception as e:
        logger.debug(f"cookbook tasks/status fetch failed: {e}")

    # Local process scan — runs in a worker thread so it doesn't block.
    external = await asyncio.to_thread(_scan_running_model_processes)

    merged: List[Dict[str, Any]] = []
    merged.extend(cookbook_tasks)
    # Dedupe: if a process's PID is already mentioned by a cookbook task
    # (cookbook may track the PID via session_id), skip it.
    cookbook_pids = set()
    for t in cookbook_tasks:
        if isinstance(t, dict) and t.get("pid"):
            cookbook_pids.add(t["pid"])
    for p in external:
        if p.get("pid") not in cookbook_pids:
            merged.append(p)

    if not merged:
        return {
            "output": "No model servers currently running (cookbook task tracker empty; /proc scan found no vLLM / sglang / MLX / llama.cpp / Ollama / ComfyUI / A1111 / Fooocus / InvokeAI / TGI / Aphrodite / Triton / Diffusers processes).",
            "exit_code": 0,
        }

    # Sort so the agent sees what's actually LIVE first. Stopped/error/
    # completed tasks are mostly historical noise — they shouldn't lead
    # the list when something is genuinely serving.
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
                # Prefer a window around a Python traceback if one exists,
                # falling back to the last 30 lines. The previous 6-line
                # tail showed only the post-crash bash prompt / neofetch
                # banner ("Locale: C / Ubuntu_Odysseus ❯") — useless for
                # diagnosis. The traceback we want is usually 50-200 lines
                # earlier in the buffer.
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
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    import shlex
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
        try:
            remote, sport = _validate_cookbook_ssh_target(remote, sport)
        except HTTPException as e:
            return {"error": str(getattr(e, "detail", e)), "exit_code": 1}
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


async def do_stop_served_model(content: str, owner: Optional[str] = None) -> Dict:
    """Stop a running model server by killing its tmux session (remote-aware)."""
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


async def do_tail_serve_output(content: str, owner: Optional[str] = None) -> Dict:
    """Capture the last N lines of a cookbook task's tmux pane — remote-aware.

    Used by the agent to debug a failed/stuck serve: list_served_models tells
    you the task is `crashed`, this tool returns the actual stderr/traceback
    so the agent can match it against a known fix (compute_89 nvcc mismatch,
    flashinfer version mismatch, OOM, missing kernels, etc.) and decide
    whether to relaunch via serve_model with new flags.
    """
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    import shlex
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": "session_id is required (from list_served_models)", "exit_code": 1}
    import re as _re
    if not _re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
        return {"error": "Invalid session_id format", "exit_code": 1}
    try:
        tail = int(args.get("tail") or 400)
    except (TypeError, ValueError):
        tail = 400
    tail = max(20, min(tail, 4000))
    headers = _internal_headers()
    remote = _string_arg(args.get("remote_host") or args.get("host"))
    sport = _string_arg(args.get("ssh_port"))
    # Resolve host from cookbook state if caller didn't pass one — same
    # lookup _cookbook_kill_session uses.
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
    if remote:
        try:
            remote, sport = _validate_cookbook_ssh_target(remote, sport)
        except HTTPException as e:
            return {"error": str(getattr(e, "detail", e)), "exit_code": 1}

    # Prefer the persisted /tmp/odysseus-tmux/SESSION.log file over the
    # live tmux pane. The pane is what the user would see scrolling on
    # their screen — including the post-crash neofetch banner and the
    # idle bash prompt that overwrites the actual traceback the moment
    # vllm exits. The log file is the raw stdout/stderr of the wrapped
    # process and survives the crash unchanged. We only fall back to
    # the pane when the log file doesn't exist (older sessions launched
    # before the tmux+tee wrapper was added).
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
        # Dedupe download-progress noise. A 100-shard HF download produces
        # tens of thousands of `model-NN-of-MM.safetensors: 91%|...` lines
        # that all look the same to the agent and drown the actual error.
        # Keep only one sample per (file, decile-percent) bucket.
        import re as _re2
        lines = output_text.splitlines()
        dedup_lines = []
        seen_progress = set()
        progress_re = _re2.compile(r"^([\w./\-]+):\s+(\d+)%")
        for ln in lines:
            m = progress_re.match(ln.strip())
            if m:
                key = (m.group(1), int(m.group(2)) // 10)  # bucket by 10%
                if key in seen_progress:
                    continue
                seen_progress.add(key)
            dedup_lines.append(ln)
        output_text = "\n".join(dedup_lines)
        # Hard cap so the agent doesn't blow its token budget.
        MAX_CHARS = 8000
        if len(output_text) > MAX_CHARS:
            output_text = "…(earlier output truncated)…\n" + output_text[-MAX_CHARS:]
        if not output_text:
            output_text = (
                f"No log output captured yet for {session_id} on {host_label}. "
                "This usually means the tmux wrapper has started but the model process "
                "has not printed anything yet. Do not stop here: call list_served_models "
                "again to check whether it is still loading, ready, or crashed; if it is "
                "still not ready, call tail_serve_output again with a larger tail after "
                "the next status check."
            )
        return {
            "output": output_text,
            "session_id": session_id,
            "host": host_label,
            "tail_lines": tail,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_list_downloads(content: str, owner: Optional[str] = None) -> Dict:
    """List in-flight model downloads (filters /api/cookbook/tasks/status to type=download)."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
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


async def do_cancel_download(content: str, owner: Optional[str] = None) -> Dict:
    """Cancel a model download by killing its tmux session (remote-aware)."""
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


async def do_search_hf_models(content: str, owner: Optional[str] = None) -> Dict:
    """Search HuggingFace via the cookbook /api/cookbook/hf-latest endpoint."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
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


async def do_adopt_served_model(content: str, owner: Optional[str] = None) -> Dict:
    """Register an externally-launched model server (bash + tmux + ssh, or
    anything else) into the Cookbook so it appears in list_served_models,
    can be stopped via stop_served_model, and is added to the user's
    endpoint list for chat. Use this when a model was started outside
    the cookbook's serve flow but you want first-class tracking.

    Args (JSON):
      host:          "user@192.0.2.10" (or omit for localhost)
      tmux_session:  "minimax-m27"  (existing tmux session name)
      model:         "cyankiwi/MiniMax-M2.7-AWQ-4bit" (HF repo or display name)
      port:          8000
      name:          optional display name (defaults to model basename)
      add_endpoint:  bool (default true) — also register as a chat endpoint
    """
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    import shlex
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    host = _string_arg(args.get("host") or args.get("remote_host"))
    sess = (args.get("tmux_session") or args.get("session_id") or "").strip()
    model = (args.get("model") or args.get("repo_id") or "").strip()
    port = args.get("port") or 8000
    display_name = (args.get("name") or "").strip() or (model.split("/")[-1] if "/" in model else model)
    add_endpoint = args.get("add_endpoint", True)

    if not sess or not model:
        return {"error": "tmux_session and model are required", "exit_code": 1}

    # Verify tmux session exists on the target host
    if host:
        try:
            host, _ = _validate_cookbook_ssh_target(host)
        except HTTPException as e:
            return {"error": str(getattr(e, "detail", e)), "exit_code": 1}

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

    # Best-effort health check — does port respond to /v1/models?
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

    # Read+modify+write cookbook state. APPEND a task entry; do NOT
    # overwrite the whole file (that'd nuke presets).
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        return {"error": f"could not read cookbook state: {e}", "exit_code": 1}
    if not isinstance(state, dict):
        state = {}
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    # Skip duplicate adopt of the same session
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

    # Optionally register as a chat endpoint
    endpoint_msg = ""
    if add_endpoint:
        # Resolve host to a URL. SSH form `user@host` → just take host.
        host_only = host.split("@", 1)[-1] if host else "localhost"
        endpoint_url = f"http://{host_only}:{int(port)}/v1"
        try:
            from src.tool_implementations import do_manage_endpoints  # avoid forward ref issues
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


async def do_list_cookbook_servers(content: str, owner: Optional[str] = None) -> Dict:
    """List the cookbook's configured servers and which one is the
    current default. Use this to decide where to download/serve a
    model, or to show the user options when the target host is
    ambiguous."""
    servers = await _cookbook_servers()
    hosts = servers.get("hosts") or []
    default = servers.get("default_host") or ""
    if not hosts:
        return {"output": "No cookbook servers configured. Downloads/serves default to localhost.", "servers": [], "default_host": "", "exit_code": 0}
    # Resolve which server is the default by its friendly name too.
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


async def do_list_serve_presets(content: str, owner: Optional[str] = None) -> Dict:
    """List saved serve presets from cookbook_state.json. Each preset
    is a launch template: name, model, host, port, cmd. Use this to
    discover what the user has previously configured so you can
    launch by preset instead of fabricating tmux commands."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
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


async def do_serve_preset(content: str, owner: Optional[str] = None) -> Dict:
    """Launch a saved serve preset by name. Resolves the preset's
    cmd + host + model from cookbook_state.json, then calls the
    standard model/serve endpoint. Saves the agent from having to
    reinvent tmux launch commands the user already saved."""
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
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
    # Match by exact name first, then case-insensitive substring.
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
    # Resolve per-host env settings the same way the UI does — pulls
    # env_prefix (source ~/vllm-env/bin/activate), gpus, hf_token,
    # etc. from cookbook_state.env so launches actually find vllm.
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


async def do_list_cached_models(content: str, owner: Optional[str] = None) -> Dict:
    """List models already cached locally and/or on remote hosts.

    With no `host` arg, scans EVERY configured Cookbook server (and local)
    and aggregates — so the agent sees the full inventory in one call
    instead of having to query each server individually.
    """
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE  # shared, lives in facade
    import httpx
    try:
        args = _parse_tool_args(content) if content.strip() else {}
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    raw_host = (args.get("host") or "").strip()
    headers = _internal_headers()

    async def _scan_one(host_label: str, host_val: str, ssh_port: str = "",
                        platform: str = "", model_dir: str = "") -> list:
        """Hit /api/model/cached for one host; tag each returned model with its source."""
        p: Dict[str, str] = {}
        if host_val:
            p["host"] = host_val
        # Caller-provided override beats per-server config beats nothing.
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

    # When the caller specifies a host explicitly, scan only that one (old behaviour).
    # Otherwise iterate every configured server + local so the agent doesn't
    # have to repeat the call per server.
    try:
        # Pull configured servers from cookbook state (used for resolving
        # modelDirs both when caller specifies a host and when we scan all).
        servers: list = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                st = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                st_data = st.json() if st.headers.get("content-type", "").startswith("application/json") else {}
            servers = (st_data.get("env", {}) or {}).get("servers") or []
        except Exception as e:
            logger.debug(f"server list fetch failed: {e}")
            st_data = {}

        def _dirs_for(server_record: Dict[str, Any]) -> str:
            """Comma-joined modelDirs from a saved server record (Settings).

            Filters out the HF cache (~/.cache/huggingface/hub) — the backend
            scan script always scans it by default, so re-passing it as an
            extra model_dir is redundant AND confuses some path-handling
            edge cases where the extra dir suppresses the deeper scan.
            We only need to forward the NON-default dirs (e.g. /mnt/HADES/models).
            """
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
            # Find this host's saved record so its modelDirs apply too.
            srv = next(
                (s for s in servers if isinstance(s, dict)
                 and (s.get("name") == raw_host or s.get("host") == host or s.get("host") == raw_host)),
                {},
            )
            models = await _scan_one(raw_host, host, model_dir=_dirs_for(srv))
        else:
            # Always include local. Local's saved record is the one with no host.
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
                    name,
                    host_val,
                    ssh_port=str(s.get("port") or ""),
                    platform=s.get("platform") or "",
                    model_dir=_dirs_for(s),
                ))
            results = await asyncio.gather(*scans, return_exceptions=False)
            # Dedupe by (host, repo_id) — same model could appear in both HF cache + Ollama list.
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
            # Cache scans can miss models downloaded into the HF default cache
            # when the server has no explicit model_dir configured. Surface
            # completed Cookbook download tasks so the agent doesn't conclude
            # a model is absent and re-download it.
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
        # Multi-host scan: group by host so the agent sees inventory per server.
        # Single-host scan: flat list (matches old output shape).
        if raw_host:
            lines = [f"{len(models)} cached model(s) on {raw_host}:"]
            for m in models:
                name = m.get("repo_id", "?")
                sz = m.get("size") or (f"{m.get('size_bytes', 0) / (1024**3):.1f}GB" if m.get("size_bytes") else "")
                inc = " (incomplete)" if m.get("has_incomplete") else ""
                kind = " [diffusion]" if m.get("is_diffusion") else ""
                lines.append(f"- {name}{kind} — {sz}{inc}")
        else:
            from collections import defaultdict as _dd
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
