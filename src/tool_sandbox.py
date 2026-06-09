"""
src/tool_sandbox.py

Optional, opt-in per-call container sandbox for the agent ``bash`` / ``python``
tools — confines the case where those tools otherwise run in-process with the
app's full environment and no filesystem/network isolation.

When enabled, each ``bash`` / ``python`` tool call runs inside a fresh, hardened,
ephemeral container (``docker run --rm ...``): an optional gVisor-capable runtime,
``--cap-drop=ALL``, ``--read-only`` root fs, ``no-new-privileges``, a stricter
seccomp profile, pids/mem/cpu/ulimit caps, NO host-environment passthrough, and no
network by default. The container is torn down after the one-shot command (and
force-removed on timeout / cancellation, since killing the ``docker run`` client
does not stop the container).

Off by default. ``sandbox_enabled()`` is True only when ALL of the following hold:

  1. ``TOOL_SANDBOX`` (env) / ``tool_sandbox`` (setting) == ``"container"``
  2. ``ODYSSEUS_DESKTOP`` != ``"1"`` (don't sandbox the single-user desktop build)
  3. the ``docker`` CLI is present on PATH

If any is false, ``tool_execution._direct_fallback`` runs the command directly
exactly as before, so default behaviour is unchanged and the wrapper degrades
gracefully when Docker is absent.

This is ADDITIONAL confinement layered on top of the existing admin gate
(``src/tool_security``); it never replaces an auth check.

Every limit/image/runtime value has a safe default and is overridable via a
``TOOL_SANDBOX_*`` env var or the matching lowercase tenant/global setting, so
the hardened run config can be tuned without code changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Defaults. Image refs point at a private registry that only a provisioned cloud
# host will have; the sandbox is off everywhere else, so these are never reached
# until an operator opts in and builds docker/sandbox/Dockerfile.tenant-runtime*.
_DEFAULTS: Dict[str, str] = {
    "tool_sandbox": "off",                  # off | container
    "tool_sandbox_runtime": "runc",         # runc | runsc | kata-runtime | ...
    "tool_sandbox_image": "registry.internal/tenant-runtime:python",
    "tool_sandbox_shell_image": "registry.internal/tenant-runtime:shell",
    "tool_sandbox_network": "none",         # none | <internal network name>
    "tool_sandbox_memory": "512m",
    "tool_sandbox_cpus": "0.5",
    "tool_sandbox_pids": "128",
    "tool_sandbox_nofile": "256",
    "tool_sandbox_nproc": "64",
    "tool_sandbox_tmpfs_size": "64m",
    "tool_sandbox_seccomp": "",             # path; "" => the in-repo artifact
}

# Minimal EXPLICIT container environment. NEVER inherit the host/orchestrator env
# (spec section 4.7): `docker run` starts with an empty env unless we add -e, so
# secrets stay out by construction. HOME points at the writable tmpfs because the
# root fs is read-only.
_SANDBOX_ENV: Dict[str, str] = {
    "TERM": "xterm-256color",
    "COLUMNS": "120",
    "LINES": "40",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "HOME": "/tmp",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}

# Linux MAX_ARG_STRLEN is 128 KiB per single argv argument; code is passed as one
# argument, so cap well under that.
_MAX_CODE_BYTES = 120_000

_docker_path_cache: Optional[str] = None
_docker_probed = False


def _cfg(key: str) -> str:
    """Resolve a TOOL_SANDBOX_* value: env override (UPPER) > setting (lower) > default."""
    env = os.getenv(key.upper())
    if env:
        return env.strip()
    try:
        from src.settings import get_setting

        val = get_setting(key, None)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    except Exception:
        pass
    return _DEFAULTS.get(key, "")


def _docker_available() -> bool:
    """True if a `docker` CLI is on PATH. Cached (PATH doesn't change at runtime)."""
    global _docker_path_cache, _docker_probed
    if not _docker_probed:
        _docker_path_cache = shutil.which("docker")
        _docker_probed = True
    return _docker_path_cache is not None


def _reset_docker_probe() -> None:
    """Test hook: clear the cached docker-availability probe."""
    global _docker_path_cache, _docker_probed
    _docker_path_cache = None
    _docker_probed = False


def sandbox_enabled() -> bool:
    """Whether bash/python tool calls should run in a hardened container.

    Opt-in and off by default: True only when TOOL_SANDBOX=container, the call is
    not on the desktop build, and a docker CLI is available. Deployment-agnostic —
    any operator can turn it on; nothing here requires a specific deployment mode.
    """
    if _cfg("tool_sandbox").lower() != "container":
        return False
    if os.getenv("ODYSSEUS_DESKTOP") == "1":
        return False
    if not _docker_available():
        return False
    return True


def _current_tenant_id() -> Optional[str]:
    try:
        from core.tenant import get_current_tenant_id

        return get_current_tenant_id()
    except Exception:
        return None


def _seccomp_path() -> Optional[str]:
    """Absolute path to the seccomp profile, or None to omit the flag."""
    configured = _cfg("tool_sandbox_seccomp")
    if configured:
        return configured if os.path.isfile(configured) else None
    # Repo artifact: <repo root>/docker/sandbox/seccomp-tenant.json
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(repo_root, "docker", "sandbox", "seccomp-tenant.json")
    return candidate if os.path.isfile(candidate) else None


def _build_docker_argv(tool: str, name: str, content: str) -> list[str]:
    """Build the hardened `docker run` argv for a one-shot tool call (spec section 3)."""
    mem = _cfg("tool_sandbox_memory")
    nofile = _cfg("tool_sandbox_nofile")
    nproc = _cfg("tool_sandbox_nproc")
    runtime = _cfg("tool_sandbox_runtime")

    argv: list[str] = [
        "docker", "run", "--rm",
        "--name", name,
        "--user", "10001:10001",                       # non-root in-container
        "--read-only",                                 # immutable root fs
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={_cfg('tool_sandbox_tmpfs_size')}",
        "--cap-drop", "ALL",                           # drop every capability
        "--security-opt", "no-new-privileges:true",    # block setuid escalation
        "--pids-limit", _cfg("tool_sandbox_pids"),     # fork-bomb ceiling
        "--memory", mem,
        "--memory-swap", mem,                          # == memory => no swap
        "--cpus", _cfg("tool_sandbox_cpus"),
        "--ulimit", f"nofile={nofile}:{nofile}",
        "--ulimit", f"nproc={nproc}:{nproc}",
        "--network", _cfg("tool_sandbox_network"),     # "none" by default
        "--restart", "no",
        "--label", "odysseus.kind=tenant-tool",
        "--label", f"odysseus.tool={tool}",
    ]

    # gVisor / alternative runtime (Phase 2). runc is the daemon default, so only
    # pass --runtime when a non-default is configured.
    if runtime and runtime != "runc":
        argv += ["--runtime", runtime]

    # Stricter seccomp profile on top of the Docker default (defense-in-depth;
    # spec section 4.6). Omitted only if the profile file can't be found.
    seccomp = _seccomp_path()
    if seccomp:
        argv += ["--security-opt", f"seccomp={seccomp}"]

    tenant_id = _current_tenant_id()
    if tenant_id:
        argv += ["--label", f"odysseus.tenant={tenant_id}"]

    for k, v in _SANDBOX_ENV.items():
        argv += ["-e", f"{k}={v}"]

    if tool == "python":
        # Image ENTRYPOINT is python3; append `-I -c <code>`.
        argv += [_cfg("tool_sandbox_image"), "-I", "-c", content]
    else:  # bash
        # Image ENTRYPOINT is /bin/sh; append `-c <command>`.
        argv += [_cfg("tool_sandbox_shell_image"), "-c", content]

    return argv


async def _force_remove(name: str) -> None:
    """Best-effort `docker rm -f <name>` to reap a container the client couldn't."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        pass


async def run_sandboxed(
    tool: str,
    content: str,
    *,
    timeout: float,
    progress_cb=None,
) -> Dict:
    """Run a one-shot bash/python command in a hardened container.

    Returns the SAME dict shape as the direct ``_direct_fallback`` branches:
      success -> {"output": str, "exit_code": int}
      timeout -> {"error": str, "exit_code": 124, "stdout": str, "stderr": str}
      failure -> {"error": str, "exit_code": 1}
    """
    # Reuse the existing streaming/timeout engine and formatting. Imported lazily
    # to avoid a circular import (tool_execution lazy-imports this module).
    from src.tool_execution import (
        MAX_OUTPUT_CHARS,
        _run_subprocess_streaming,
        _truncate,
    )

    if tool not in ("bash", "python"):
        return {"error": f"tool_sandbox: unsupported tool '{tool}'", "exit_code": 1}

    if len(content.encode("utf-8", errors="ignore")) > _MAX_CODE_BYTES:
        return {
            "error": (
                f"{tool}: code too large for sandboxed execution "
                f"(>{_MAX_CODE_BYTES} bytes) - disable TOOL_SANDBOX or split the command"
            ),
            "exit_code": 1,
        }

    name = f"odysseus-tool-{uuid.uuid4().hex[:12]}"
    argv = _build_docker_argv(tool, name, content)

    # Audit log (spec §7): record every sandbox spawn with non-sensitive metadata
    # only — never the command/code, which can contain secrets.
    logger.info(
        "tool_sandbox: spawning %s sandbox name=%s tenant=%s runtime=%s",
        tool, name, _current_tenant_id() or "-", _cfg("tool_sandbox_runtime"),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        # docker disappeared between the probe and exec, so fail to a clear error
        # rather than crashing the agent loop.
        logger.warning("tool_sandbox: docker not found at exec time")
        return {"error": f"{tool}: sandbox runtime unavailable (docker not found)", "exit_code": 1}

    try:
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc, timeout=timeout, progress_cb=progress_cb,
        )
    except asyncio.CancelledError:
        await _force_remove(name)
        raise
    except Exception as e:  # noqa: BLE001 - surface any spawn/stream error as a tool error
        await _force_remove(name)
        logger.exception("tool_sandbox: sandboxed run failed")
        return {"error": f"{tool}: sandbox execution error: {e}", "exit_code": 1}

    if timed_out:
        await _force_remove(name)
        return {
            "error": f"{tool}: timed out after {int(timeout)}s - sandbox killed",
            "exit_code": 124,
            "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
            "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
        }

    output = stdout.rstrip()
    err = stderr.rstrip()
    if err:
        output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
    output = _truncate(output, MAX_OUTPUT_CHARS)
    return {"output": output or "(no output)", "exit_code": rc or 0}
