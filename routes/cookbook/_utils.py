"""Cookbook routes — model download, serve, cache scanning, and cookbook state sync."""

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Depends

from src.auth_helpers import require_user
from src.constants import COOKBOOK_STATE_FILE
from pydantic import BaseModel

from core.middleware import require_admin
from routes._validators import validate_remote_host, validate_ssh_port
from core.platform_compat import (
    IS_WINDOWS,
    detached_popen_kwargs,
    find_bash,
    kill_process_tree,
    pid_alive,
    safe_chmod,
    which_tool,
)
from routes.shell_routes import TMUX_LOG_DIR
from src.host_docker_access import (
    HOST_DOCKER_ACCESS_HINT,
    HOST_DOCKER_SOCKET_PATH,
    host_docker_access_enabled,
    local_docker_available,
    running_in_container,
)
from routes.cookbook_output import (
    error_aware_output_tail, classify_dead_download,
    HF_CACHE_COMPLETE_PROBE, HF_CACHE_INCOMPLETE_PROBE,
)

logger = logging.getLogger(__name__)

from routes.cookbook_helpers import (
    _SESSION_ID_RE, _validate_repo_id, _validate_serve_model_id, _validate_include, _validate_token,
    _validate_local_dir, _validate_gpus, _shell_path,
    _ps_squote, _bash_squote, _validate_serve_cmd, _parse_serve_phase, OLLAMA_MISSING_HINT,
    _safe_env_prefix, _local_tooling_path_export, _append_serve_preflight_exit_lines,
    _append_serve_exit_code_lines, _append_llama_cpp_linux_accel_build_lines, _cached_model_scan_script,
    load_stored_hf_token,
    _append_vllm_linux_preflight_lines, _ollama_bind_from_cmd, _pip_install_fallback_chain,
    _pip_install_no_cache, _user_shell_path_bootstrap, _venv_safe_local_pip_install_cmd,
    _windows_safe_local_pip_install_cmd, _diagnose_serve_output, run_ssh_command_async,
    _append_pip_install_runner_lines, _pip_install_command_without_break_system_packages,
    _normalize_llama_cpp_python_cache_types,
    ModelDownloadRequest, ServeRequest,
)

_HF_TOKEN_STATUS_SNIPPET = (
    'if [ -n "$HF_TOKEN" ]; then '
    'echo "[odysseus] HF token: applied"; '
    'else '
    'echo "[odysseus] HF token: NOT SET — gated/private models will be denied. '
    'Add one in Odysseus Cookbook -> Settings -> HuggingFace Token."; '
    'fi'
)


def _venv_root_from_serve_cmd(cmd: str) -> str:
    """Best-effort venv root from an absolute venv python in a serve command."""
    try:
        parts = shlex.split(cmd or "")
    except Exception:
        parts = (cmd or "").split()
    for part in parts:
        if re.search(r"/bin/python(?:3(?:\.\d+)?)?$", part or ""):
            return re.sub(r"/bin/python(?:3(?:\.\d+)?)?$", "", part)
    return ""


def _append_venv_nvidia_library_path_lines(lines: list[str], *, cmd: str = "") -> None:
    """Expose NVIDIA CUDA runtime wheels bundled inside the active venv.

    SGLang/vLLM wheels can depend on CUDA libraries shipped as Python packages
    under site-packages/nvidia. Activating the venv puts Python packages on
    sys.path, but the dynamic loader still cannot find libraries such as
    libnvrtc.so.13 unless those package lib dirs are on LD_LIBRARY_PATH.
    """
    venv_root = _venv_root_from_serve_cmd(cmd)
    lines.append(f'_ODY_VENV_FOR_LIBS="${{VIRTUAL_ENV:-{_bash_squote(venv_root)}}}"')
    lines.append('if [ -n "$_ODY_VENV_FOR_LIBS" ] && [ -d "$_ODY_VENV_FOR_LIBS" ]; then')
    lines.append('  for _ody_nvlib in "$_ODY_VENV_FOR_LIBS"/lib/python*/site-packages/nvidia/cu13/lib "$_ODY_VENV_FOR_LIBS"/lib/python*/site-packages/nvidia/cu12/lib "$_ODY_VENV_FOR_LIBS"/lib/python*/site-packages/nvidia/cuda_nvrtc/lib "$_ODY_VENV_FOR_LIBS"/lib/python*/site-packages/nvidia/cuda_runtime/lib "$_ODY_VENV_FOR_LIBS"/lib/python*/site-packages/nvidia/cublas/lib "$_ODY_VENV_FOR_LIBS"/lib/python*/site-packages/nvidia/cudnn/lib; do')
    lines.append('    [ -d "$_ody_nvlib" ] && export LD_LIBRARY_PATH="$_ody_nvlib:${LD_LIBRARY_PATH:-}"')
    lines.append('  done')
    lines.append('fi')


def _serve_port_from_cmd(cmd: str) -> str:
    m = re.search(r"--port(?:=|\s+)(\d+)", cmd or "")
    return m.group(1) if m else ""


def _append_openai_port_preflight_lines(lines: list[str], *, cmd: str, expected_model: str) -> None:
    port = _serve_port_from_cmd(cmd)
    if not port:
        return
    lines.append(f"ODYSSEUS_SERVE_PORT='{_bash_squote(port)}'")
    lines.append(f"ODYSSEUS_EXPECTED_MODEL='{_bash_squote(expected_model)}'")
    lines.append("if [ -n \"$ODYSSEUS_SERVE_PORT\" ]; then")
    lines.append("  python3 - \"$ODYSSEUS_SERVE_PORT\" \"$ODYSSEUS_EXPECTED_MODEL\" <<'PY'")
    lines.append("import json, sys, urllib.request")
    lines.append("port = sys.argv[1]")
    lines.append("expected = (sys.argv[2] or '').strip()")
    lines.append("url = f'http://127.0.0.1:{port}/v1/models'")
    lines.append("try:")
    lines.append("    with urllib.request.urlopen(url, timeout=1.5) as r:")
    lines.append("        data = json.loads(r.read().decode('utf-8', 'replace') or '{}')")
    lines.append("except Exception:")
    lines.append("    raise SystemExit(0)")
    lines.append("models = [str(x.get('id') or '') for x in data.get('data', []) if isinstance(x, dict)]")
    lines.append("def base(s): return s.lower().split('/')[-1]")
    lines.append("match = bool(expected) and any((m.lower() == expected.lower() or base(m) == base(expected) or base(expected) in m.lower() or base(m) in expected.lower()) for m in models)")
    lines.append("print(f'ERROR: Port {port} is already serving {models or [\"unknown\"]}.')")
    lines.append("if expected and not match:")
    lines.append("    print(f'ERROR: Cookbook was about to launch {expected}, but this port is occupied by a different model. Stop the old server or choose another port.')")
    lines.append("else:")
    lines.append("    print('ERROR: Stop the existing server or choose another port before launching a duplicate serve.')")
    lines.append("raise SystemExit(98)")
    lines.append("PY")
    lines.append("  _ody_port_ec=$?")
    lines.append("  if [ \"$_ody_port_ec\" -ne 0 ]; then ODYSSEUS_PREFLIGHT_EXIT=\"$_ody_port_ec\"; fi")
    lines.append("fi")

_OLLAMA_SIDECAR_CONTAINERS = {"ollama-test", "ollama-rocm"}
_UNSAFE_DOCKER_EXEC_CHARS = frozenset(";&|<>$`\r\n")
_SAFE_OLLAMA_MODEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SAFE_OLLAMA_FILE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_generated_ollama_docker_exec_cmd(cmd: str | None) -> bool:
    """Match only the fixed Docker exec shapes generated by Cookbook."""
    if not cmd or any(char in cmd for char in _UNSAFE_DOCKER_EXEC_CHARS):
        return False
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if len(parts) < 4 or parts[:2] != ["docker", "exec"]:
        return False
    container, executable = parts[2:4]
    if container not in _OLLAMA_SIDECAR_CONTAINERS:
        return False
    if container == "ollama-rocm" and executable == "ollama":
        return (
            len(parts) == 6
            and parts[4] == "show"
            and _SAFE_OLLAMA_MODEL_TOKEN_RE.fullmatch(parts[5]) is not None
        )
    if container != "ollama-test" or executable != "ollama-import":
        return False
    if len(parts) not in {7, 8}:
        return False
    model, name, context_size = parts[4:7]
    return (
        _SAFE_OLLAMA_MODEL_TOKEN_RE.fullmatch(model) is not None
        and _SAFE_OLLAMA_FILE_TOKEN_RE.fullmatch(name) is not None
        and re.fullmatch(r"[0-9]+", context_size) is not None
        and (
            len(parts) == 7
            or _SAFE_OLLAMA_FILE_TOKEN_RE.fullmatch(parts[7]) is not None
        )
    )


def _missing_binary_message(
    binary: str,
    target: str,
    *,
    local_host_docker_blocked: bool = False,
) -> str:
    if binary == "tmux":
        return (
            f"tmux is required for Cookbook background downloads/serves on {target}. "
            "Install it with your OS package manager, or run Cookbook server setup for that server."
        )
    if binary == "docker":
        if local_host_docker_blocked:
            return HOST_DOCKER_ACCESS_HINT
        return (
            f"Docker is required by this Cookbook launch command on {target}, but the docker CLI was not found. "
            "Install Docker and make sure this user can run `docker`, then retry."
        )
    return f"{binary} is required on {target}, but it was not found."


async def _remote_binary_available(
    remote: str,
    ssh_port: str | None,
    binary: str,
    *,
    windows: bool = False,
) -> bool:
    port = ssh_port or ""
    port_args = ["-p", port] if port and port != "22" else []
    if windows:
        check = f'powershell -NoProfile -Command "if (Get-Command {binary} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 127 }}"'
    else:
        check = f'PATH="$HOME/.local/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; command -v {shlex.quote(binary)} >/dev/null 2>&1'
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "StrictHostKeyChecking=no",
            *port_args,
            remote,
            check,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


def _remote_posix_path_prefix() -> str:
    return 'PATH="$HOME/.local/bin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '


def _remote_tmux_command(*args: str) -> str:
    """Shell command for remote tmux when non-login SSH has a thin PATH."""
    tmux = (
        'ODYSSEUS_TMUX="$(command -v tmux '
        '|| command -v /opt/homebrew/bin/tmux '
        '|| command -v /usr/local/bin/tmux '
        '|| command -v /usr/bin/tmux '
        '|| true)"; '
        'if [ -z "$ODYSSEUS_TMUX" ]; then echo "tmux not found" >&2; exit 127; fi; '
    )
    quoted = " ".join(shlex.quote(str(arg)) for arg in args)
    return f'{_remote_posix_path_prefix()}{tmux}"$ODYSSEUS_TMUX" {quoted}'


def _remote_tmux_launch_command(session_id: str, runner: str) -> str:
    """Shell command that chmods a runner and starts it in remote tmux."""
    tmux = (
        'ODYSSEUS_TMUX="$(command -v tmux '
        '|| command -v /opt/homebrew/bin/tmux '
        '|| command -v /usr/local/bin/tmux '
        '|| command -v /usr/bin/tmux '
        '|| true)"; '
        'if [ -z "$ODYSSEUS_TMUX" ]; then echo "tmux not found" >&2; exit 127; fi; '
    )
    sid = shlex.quote(str(session_id))
    runner_q = shlex.quote(str(runner))
    runner_exec = shlex.quote(f"./{runner}")
    return (
        f'{_remote_posix_path_prefix()}{tmux}'
        f'chmod +x {runner_q} && '
        f'"$ODYSSEUS_TMUX" set-option -g history-limit 100000 2>/dev/null; '
        f'"$ODYSSEUS_TMUX" new-session -d -s {sid} {runner_exec}'
    )


async def _binary_available(
    binary: str,
    remote: str | None,
    ssh_port: str | None,
    *,
    windows: bool = False,
    in_container: bool | None = None,
    environ=None,
    socket_path: str = HOST_DOCKER_SOCKET_PATH,
) -> bool:
    if remote:
        return await _remote_binary_available(
            remote,
            ssh_port,
            binary,
            windows=windows,
        )
    cli_available = shutil.which(binary) is not None
    if binary != "docker":
        return cli_available
    return local_docker_available(
        cli_available=cli_available,
        in_container=in_container,
        environ=environ,
        socket_path=socket_path,
    )



def _local_ollama_docker_fallback_available(
    *,
    in_container: bool | None = None,
    environ: dict[str, str] | None = None,
    socket_path: str = HOST_DOCKER_SOCKET_PATH,
) -> bool:
    return local_docker_available(
        cli_available=shutil.which("docker") is not None,
        in_container=in_container,
        environ=environ,
        socket_path=socket_path,
    )


def _local_ollama_docker_access_blocked(
    *,
    in_container: bool | None = None,
    environ: dict[str, str] | None = None,
    socket_path: str = HOST_DOCKER_SOCKET_PATH,
) -> bool:
    containerized = running_in_container() if in_container is None else in_container
    if not containerized or shutil.which("docker") is None:
        return False
    return not _local_ollama_docker_fallback_available(
        in_container=containerized,
        environ=environ,
        socket_path=socket_path,
    )


def _append_local_ollama_download_command_lines(
    lines: list[str],
    ollama_cmd: str,
    *,
    docker_fallback_available: bool,
    docker_fallback_blocked: bool,
) -> None:
    lines.append('if command -v ollama >/dev/null 2>&1; then')
    lines.append(f'  ODYSSEUS_OLLAMA_PULL_CMD={shlex.quote(ollama_cmd)}')
    if docker_fallback_available:
        lines.append('elif command -v docker >/dev/null 2>&1; then')
        lines.append("  ODYSSEUS_OLLAMA_CONTAINER=\"$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^(ollama-rocm|ollama-test)$' | head -1)\"")
        lines.append('  if [ -n "$ODYSSEUS_OLLAMA_CONTAINER" ]; then')
        lines.append(f'    ODYSSEUS_OLLAMA_PULL_CMD={shlex.quote("docker exec ${ODYSSEUS_OLLAMA_CONTAINER} " + ollama_cmd)}')
        lines.append('  fi')
    elif docker_fallback_blocked:
        hint = shlex.quote("ERROR: " + HOST_DOCKER_ACCESS_HINT)
        lines.append('else')
        lines.append(f"  printf '%s\\n' {hint}; exit 127")
    lines.append('fi')
    lines.append('if [ -z "$ODYSSEUS_OLLAMA_PULL_CMD" ]; then echo "ERROR: Ollama not found on this server. Install Ollama or start an ollama-rocm/ollama-test container."; exit 127; fi')
