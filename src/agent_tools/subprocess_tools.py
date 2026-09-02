import asyncio
import hashlib
import os
import re
import shutil
import sys
import time
import collections
from typing import Optional, Callable, Awaitable, Tuple, Dict
from core.platform_compat import IS_WINDOWS, find_bash
from src.constants import MAX_OUTPUT_CHARS
from src.execution_sandbox import (
    SandboxNetworkProfile,
    SandboxUnavailable,
    environment_for_sandbox_launcher,
    full_access_command,
    sandbox_command,
    sandbox_python_executable,
)
from src.process_execution import (
    FULL_ACCESS_WARNING,
    ProcessExecutionMode,
    blocked_process_result,
    configured_process_execution_mode,
    process_capability,
)

DEFAULT_BASH_TIMEOUT = 60 * 60     # 1 hour
DEFAULT_PYTHON_TIMEOUT = 60 * 60

PROGRESS_INTERVAL_S = 2.0
PROGRESS_TAIL_LINES = 12
TMUX_CAPTURE_LINES = 2000
_TMUX_ENV_SCRUBBER = "/usr/bin/env"
_TMUX_LOCKS: dict[str, asyncio.Lock] = {}
_TMUX_OWNED_SESSIONS: set[str] = set()


async def _create_bash_subprocess(command: str, **kwargs):
    """Start the compatibility Bash subprocess path for direct callers."""
    if IS_WINDOWS:
        bash = find_bash()
        if not bash:
            raise RuntimeError(
                "Git Bash is required for the Bash tool on Windows; "
                "install Git for Windows and restart Odysseus"
            )
        return await asyncio.create_subprocess_exec(bash, "-c", command, **kwargs)
    return await asyncio.create_subprocess_shell(command, **kwargs)


def _tmux_session_prefix(
    session_id: Optional[str],
    workspace: str = "",
    *,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session_id or "default")).strip("-")
    workspace_key = hashlib.sha256(
        os.path.realpath(workspace or ".").encode("utf-8", errors="replace")
    ).hexdigest()[:10]
    network_key = network_profile.value.replace("_", "-")
    return f"ody-agent-sbx-v2-{raw[:60] or 'default'}-{workspace_key}-{network_key}"


def _tmux_legacy_session_name(
    session_id: Optional[str],
    workspace: str,
    *,
    network_profile: SandboxNetworkProfile,
) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session_id or "default")).strip("-")
    workspace_key = hashlib.sha256(
        os.path.realpath(workspace or ".").encode("utf-8", errors="replace")
    ).hexdigest()[:10]
    network_key = network_profile.value.replace("_", "-")
    return f"ody-agent-sbx-v1-{raw[:60] or 'default'}-{workspace_key}-{network_key}"


def _tmux_pre_sandbox_session_name(session_id: Optional[str]) -> str:
    """Return the exact tmux name used before the sandboxed v1 format."""
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session_id or "default")).strip("-")
    return f"ody-agent-{raw[:80] or 'default'}"


def _tmux_session_name(
    session_id: Optional[str],
    workspace: str,
    *,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
    policy_key: str,
) -> str:
    if not isinstance(policy_key, str) or not policy_key.strip():
        raise ValueError("tmux sandbox sessions require a policy key")
    safe_policy_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", policy_key).strip("-")
    if not safe_policy_key:
        raise ValueError("tmux sandbox sessions require a policy key")
    return f"{_tmux_session_prefix(session_id, workspace, network_profile=network_profile)}-{safe_policy_key}"


def _tmux_policy_key(workspace_stat: os.stat_result, shell_argv: list[str]) -> str:
    encoded = "\0".join(
        [str(workspace_stat.st_dev), str(workspace_stat.st_ino), *shell_argv]
    ).encode("utf-8", errors="surrogateescape")
    return hashlib.sha256(encoded).hexdigest()[:16]


async def _run_exec(*args: str, timeout: float = 10) -> Tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={},
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "", "timeout", 124
    return (
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


async def _tmux_has_session(name: str) -> bool:
    _, _, rc = await _run_exec("tmux", "has-session", "-t", name, timeout=3)
    return rc == 0


async def _tmux_session_names() -> list[str]:
    out, err, rc = await _run_exec(
        "tmux",
        "list-sessions",
        "-F",
        "#{session_name}",
        timeout=5,
    )
    if rc == 0:
        return [line.strip() for line in out.splitlines() if line.strip()]
    detail = f"{out}\n{err}".casefold()
    if "no server running" in detail or "failed to connect to server" in detail:
        return []
    raise RuntimeError(f"failed to list tmux sessions: {(err or out).strip()}")


async def _tmux_kill_session(name: str) -> None:
    out, err, rc = await _run_exec("tmux", "kill-session", "-t", name, timeout=5)
    if rc == 0:
        _TMUX_OWNED_SESSIONS.discard(name)
        return
    detail = f"{out}\n{err}".casefold()
    if (
        "no server running" in detail
        or "session not found" in detail
        or "can't find session" in detail
    ):
        _TMUX_OWNED_SESSIONS.discard(name)
        return
    raise RuntimeError(f"failed to terminate stale tmux session {name}: {(err or out).strip()}")


async def _cleanup_stale_tmux_sessions(
    prefix: str,
    legacy_names: tuple[str, ...],
    current_name: Optional[str],
) -> None:
    """Terminate stale sessions for one logical workspace/network identity.

    ``current_name=None`` means fresh policy construction failed, so every v2
    session for this logical identity is stale and must be terminated.
    """
    existing = await _tmux_session_names()
    legacy = set(legacy_names)
    stale = {
        name
        for name in existing
        if name in legacy
        or (
            name.startswith(f"{prefix}-")
            and (current_name is None or name != current_name)
        )
    }
    for name in sorted(stale):
        await _tmux_kill_session(name)


async def _tmux_capture(name: str) -> str:
    out, _, _ = await _run_exec(
        "tmux", "capture-pane", "-p", "-J", "-S", f"-{TMUX_CAPTURE_LINES}", "-t", name,
        timeout=5,
    )
    return out


async def _tmux_send_line(name: str, line: str) -> None:
    if line:
        await _run_exec("tmux", "send-keys", "-t", name, "-l", line, timeout=5)
    await _run_exec("tmux", "send-keys", "-t", name, "C-m", timeout=5)


async def _ensure_tmux_session(
    name: str,
    cwd: str,
    shell_argv: list[str],
) -> None:
    if await _tmux_has_session(name):
        if name not in _TMUX_OWNED_SESSIONS:
            # A matching name that this process did not create is not evidence
            # of the expected namespace or launch policy. Recreate it rather
            # than sending model commands into an unverifiable host shell.
            await _tmux_kill_session(name)
        else:
            await _run_exec(
                "tmux", "send-keys", "-t", name, "stty -echo", "C-m", timeout=5
            )
            return
    if not os.path.isfile(_TMUX_ENV_SCRUBBER):
        raise RuntimeError("trusted tmux environment scrubber is unavailable")
    _, launch_error, _ = await _run_exec(
        "tmux", "new-session", "-d", "-s", name, "-c", cwd,
        _TMUX_ENV_SCRUBBER, "-i", *shell_argv,
        timeout=10,
    )
    if not await _tmux_has_session(name):
        if (
            launch_error.startswith("odysseus-seccomp-launcher:")
            or launch_error.startswith("odysseus-egress-broker:")
            or launch_error.startswith("odysseus-egress-bridge:")
            or launch_error.startswith("bwrap:")
        ):
            raise RuntimeError(
                "sandbox setup failed for the persistent shell; verify the "
                "trusted launcher and outer OCI seccomp compatibility"
            )
        raise RuntimeError(f"failed to create tmux session {name}")
    _TMUX_OWNED_SESSIONS.add(name)
    await _run_exec("tmux", "send-keys", "-t", name, "stty -echo", "C-m", timeout=5)


def _output_after_marker(capture: str, start_marker: str, end_marker: str) -> Tuple[str, bool]:
    lines = capture.splitlines()
    start_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == start_marker:
            start_idx = idx
    if start_idx < 0:
        return capture, False
    end_idx = -1
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].strip().startswith(end_marker):
            end_idx = idx
    if end_idx < 0:
        return "\n".join(lines[start_idx + 1:]), False
    return "\n".join(lines[start_idx + 1:end_idx]), True


def _extract_marker_rc(capture: str, end_marker: str) -> int:
    for line in reversed(capture.splitlines()):
        stripped = line.strip()
        if stripped.startswith(end_marker):
            suffix = stripped[len(end_marker):].strip()
            if suffix.isdigit():
                return int(suffix)
    return 0


async def _run_tmux_bash(
    content: str,
    *,
    session_id: str,
    cwd: str,
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
) -> Tuple[str, str, Optional[int], bool, str]:
    canonical_cwd = os.path.realpath(cwd)
    prefix = _tmux_session_prefix(
        session_id,
        canonical_cwd,
        network_profile=network_profile,
    )
    legacy_names = (
        _tmux_legacy_session_name(
            session_id,
            canonical_cwd,
            network_profile=network_profile,
        ),
        _tmux_pre_sandbox_session_name(session_id),
    )
    lock = _TMUX_LOCKS.setdefault(prefix, asyncio.Lock())

    async with lock:
        try:
            shell_argv = sandbox_command(
                ["/bin/bash", "--noprofile", "--norc"],
                workspace=canonical_cwd,
                network_profile=network_profile,
            )
            workspace_stat = os.stat(canonical_cwd)
        except (OSError, RuntimeError, SandboxUnavailable):
            # A previously valid persistent shell must not survive after the
            # workspace can no longer produce an acceptable sandbox policy.
            await _cleanup_stale_tmux_sessions(prefix, legacy_names, None)
            raise

        policy_key = _tmux_policy_key(workspace_stat, shell_argv)
        name = _tmux_session_name(
            session_id,
            canonical_cwd,
            network_profile=network_profile,
            policy_key=policy_key,
        )
        await _cleanup_stale_tmux_sessions(prefix, legacy_names, name)
        await _ensure_tmux_session(name, canonical_cwd, shell_argv)

        stamp = f"{int(time.time() * 1000)}-{abs(hash(content)) % 1000000}"
        start_marker = f"__ODYSSEUS_CMD_START_{stamp}__"
        end_prefix = f"__ODYSSEUS_CMD_END_{stamp}__:"
        wrapped = (
            f"printf '\\n{start_marker}\\n'\n"
            f"{content}\n"
            f"__ody_rc=$?\n"
            f"printf '\\n{end_prefix}%s\\n' \"$__ody_rc\"\n"
        )
        for line in wrapped.splitlines():
            await _tmux_send_line(name, line)

        started = time.time()
        last_tail = ""
        while True:
            capture = await _tmux_capture(name)
            body, done = _output_after_marker(capture, start_marker, end_prefix)
            tail = "\n".join(body.splitlines()[-PROGRESS_TAIL_LINES:])
            if progress_cb and tail != last_tail:
                last_tail = tail
                try:
                    await progress_cb({
                        "elapsed_s": round(time.time() - started, 1),
                        "tail": tail,
                        "tmux_session": name,
                    })
                except Exception:
                    pass
            if done:
                rc = _extract_marker_rc(capture, end_prefix)
                cleaned = _clean_tmux_command_output(body, wrapped)
                return cleaned, "", rc, False, name
            if time.time() - started > timeout:
                try:
                    await _run_exec("tmux", "send-keys", "-t", name, "C-c", timeout=3)
                except Exception:
                    pass
                cleaned = _clean_tmux_command_output(body, wrapped)
                return cleaned, "", 124, True, name
            await asyncio.sleep(0.5)


def _clean_tmux_command_output(text: str, wrapped_command: str) -> str:
    lines = text.splitlines()
    wrapped_lines = {ln.rstrip() for ln in wrapped_command.splitlines() if ln.strip()}
    cleaned = []
    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            cleaned.append(raw)
            continue
        if stripped in wrapped_lines:
            continue
        if stripped.startswith("__ody_rc=") or stripped.startswith("printf "):
            continue
        if re.fullmatch(r"(?:bash|sh)-[\d.]+\$ ?", stripped):
            continue
        if re.fullmatch(r"[\w.@:/~+-]+[#$] ?", stripped):
            continue
        cleaned.append(raw)
    return "\n".join(cleaned).strip()

async def _run_subprocess_streaming(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    started = time.time()
    stdout_full: list[str] = []
    stderr_full: list[str] = []
    tail = collections.deque(maxlen=PROGRESS_TAIL_LINES)

    async def _reader(stream, full_buf, label: str):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
            full_buf.append(decoded)
            if label == "err":
                tail.append(f"! {decoded}")
            else:
                tail.append(decoded)

    async def _progress_emitter():
        await asyncio.sleep(PROGRESS_INTERVAL_S)
        while True:
            if progress_cb:
                try:
                    await progress_cb({
                        "elapsed_s": round(time.time() - started, 1),
                        "tail": "\n".join(list(tail)),
                    })
                except Exception:
                    pass
            await asyncio.sleep(PROGRESS_INTERVAL_S)

    rd_out = asyncio.create_task(_reader(proc.stdout, stdout_full, "out"))
    rd_err = asyncio.create_task(_reader(proc.stderr, stderr_full, "err"))
    prog_task = asyncio.create_task(_progress_emitter()) if progress_cb else None

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        for t in (rd_out, rd_err):
            t.cancel()
        if prog_task is not None:
            prog_task.cancel()
        raise
    finally:
        if prog_task is not None and not prog_task.done():
            prog_task.cancel()
            try:
                await prog_task
            except (asyncio.CancelledError, Exception):
                pass
        for t in (rd_out, rd_err):
            try:
                await asyncio.wait_for(t, timeout=1)
            except Exception:
                pass

    return (
        "\n".join(stdout_full),
        "\n".join(stderr_full),
        proc.returncode,
        timed_out,
    )


def _sandbox_setup_failure(
    tool: str,
    stderr: str,
    returncode: Optional[int],
) -> Optional[Dict]:
    """Convert trusted-launcher/Bubblewrap setup failures into a safe result."""
    stripped = (stderr or "").strip()
    if stripped.startswith("odysseus-seccomp-launcher:"):
        detail = stripped.split(":", 1)[1].strip()
        return {
            "error": (
                f"{tool}: Sandbox setup failed: {detail}. "
                "No unsandboxed fallback was attempted."
            ),
            "exit_code": 1,
            "blocked": True,
        }
    if stripped.startswith(("odysseus-egress-broker:", "odysseus-egress-bridge:")):
        detail = stripped.split(":", 1)[1].strip()
        return {
            "error": (
                f"{tool}: Brokered Internet setup failed: {detail}. "
                "No raw-network or unsandboxed fallback was attempted."
            ),
            "exit_code": 1,
            "blocked": True,
        }
    if returncode and stripped.startswith("bwrap:"):
        return {
            "error": (
                f"{tool}: Bubblewrap could not establish the required private "
                "namespaces and mounts. Verify the shipped outer OCI seccomp "
                "profile and host user-namespace support. No unsandboxed "
                "fallback was attempted."
            ),
            "exit_code": 1,
            "blocked": True,
        }
    return None

class BashTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate

        if isinstance(content, dict):
            content = str(
                content.get("command")
                or content.get("cmd")
                or content.get("code")
                or ""
            )
        progress_cb = ctx.get("progress_cb")
        session_id = ctx.get("session_id")
        network_profile = ctx.get(
            "network_profile", SandboxNetworkProfile.NETWORKLESS
        )
        workspace = agent_cwd()
        execution_mode = configured_process_execution_mode()

        if execution_mode is ProcessExecutionMode.FULL_ACCESS:
            if IS_WINDOWS:
                return blocked_process_result(
                    "bash",
                    execution_mode,
                    "Full Access with retained network isolation requires Linux and Bubblewrap.",
                )
            capability = process_capability().full_access
            if not capability.supports(network_profile):
                return blocked_process_result(
                    "bash",
                    execution_mode,
                    capability.reason_for(network_profile),
                )
            try:
                argv = full_access_command(
                    ["/bin/bash", "--noprofile", "--norc", "-c", content],
                    working_directory=workspace,
                    network_profile=network_profile,
                )
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment_for_sandbox_launcher(),
                    cwd=workspace,
                )
            except (OSError, RuntimeError, SandboxUnavailable) as exc:
                return {
                    "error": f"bash: {exc}",
                    "exit_code": 1,
                    "blocked": True,
                    "execution_mode": execution_mode.value,
                }
            stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
                proc,
                timeout=DEFAULT_BASH_TIMEOUT,
                progress_cb=progress_cb,
            )
            if timed_out:
                return {
                    "error": (
                        f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — "
                        "process killed"
                    ),
                    "exit_code": 124,
                    "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                    "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                    "execution_mode": execution_mode.value,
                    "warning": FULL_ACCESS_WARNING,
                }
            setup_failure = _sandbox_setup_failure("bash", stderr, rc)
            if setup_failure:
                setup_failure["execution_mode"] = execution_mode.value
                setup_failure["warning"] = FULL_ACCESS_WARNING
                return setup_failure
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (
                    (output + "\nSTDERR: " + err).strip()
                    if output
                    else "STDERR: " + err
                )
            return {
                "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
                "exit_code": rc or 0,
                "execution_mode": execution_mode.value,
                "network_enforcement": (
                    "brokered_http_https"
                    if network_profile is SandboxNetworkProfile.BROKERED_ONLY
                    else "networkless"
                ),
                "warning": FULL_ACCESS_WARNING,
            }

        if IS_WINDOWS:
            return blocked_process_result(
                "bash",
                execution_mode,
                "Sandbox mode requires Linux with bubblewrap.",
            )
        capability = process_capability().sandbox
        if not capability.supports(network_profile):
            return blocked_process_result(
                "bash",
                execution_mode,
                capability.reason_for(network_profile),
            )

        # Persistent tmux is available only in Sandbox mode. Full Access uses
        # one-shot processes so an unsandboxed shell cannot silently outlive a
        # later mode change.
        if session_id and shutil.which("tmux"):
            try:
                stdout, stderr, rc, timed_out, tmux_session = await _run_tmux_bash(
                    content,
                    session_id=str(session_id),
                    cwd=workspace,
                    timeout=DEFAULT_BASH_TIMEOUT,
                    progress_cb=progress_cb,
                    network_profile=network_profile,
                )
            except (OSError, RuntimeError, SandboxUnavailable) as exc:
                return {
                    "error": f"bash: {exc}",
                    "exit_code": 1,
                    "blocked": True,
                    "execution_mode": execution_mode.value,
                }
            if timed_out:
                return {
                    "error": (
                        f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — "
                        "sent Ctrl-C to tmux session"
                    ),
                    "exit_code": 124,
                    "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                    "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                    "tmux_session": tmux_session,
                    "execution_mode": execution_mode.value,
                }
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (
                    (output + "\nSTDERR: " + err).strip()
                    if output
                    else "STDERR: " + err
                )
            return {
                "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
                "exit_code": rc or 0,
                "tmux_session": tmux_session,
                "execution_mode": execution_mode.value,
            }

        try:
            argv = sandbox_command(
                ["/bin/bash", "--noprofile", "--norc", "-c", content],
                workspace=workspace,
                network_profile=network_profile,
            )
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment_for_sandbox_launcher(),
                cwd=workspace,
            )
        except (OSError, RuntimeError, SandboxUnavailable) as exc:
            return {
                "error": f"bash: {exc}",
                "exit_code": 1,
                "blocked": True,
                "execution_mode": execution_mode.value,
            }
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc,
            timeout=DEFAULT_BASH_TIMEOUT,
            progress_cb=progress_cb,
        )
        if timed_out:
            return {
                "error": (
                    f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — process killed"
                ),
                "exit_code": 124,
                "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                "execution_mode": execution_mode.value,
            }
        setup_failure = _sandbox_setup_failure("bash", stderr, rc)
        if setup_failure:
            setup_failure["execution_mode"] = execution_mode.value
            return setup_failure
        output = stdout.rstrip()
        err = stderr.rstrip()
        if err:
            output = (
                (output + "\nSTDERR: " + err).strip()
                if output
                else "STDERR: " + err
            )
        return {
            "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": rc or 0,
            "execution_mode": execution_mode.value,
        }


class PythonTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate

        if isinstance(content, dict):
            content = str(content.get("code") or content.get("command") or "")
        progress_cb = ctx.get("progress_cb")
        network_profile = ctx.get(
            "network_profile", SandboxNetworkProfile.NETWORKLESS
        )
        workspace = agent_cwd()
        execution_mode = configured_process_execution_mode()

        if execution_mode is ProcessExecutionMode.FULL_ACCESS:
            if IS_WINDOWS:
                return blocked_process_result(
                    "python",
                    execution_mode,
                    "Full Access with retained network isolation requires Linux and Bubblewrap.",
                )
            capability = process_capability().full_access
            if not capability.supports(network_profile):
                return blocked_process_result(
                    "python",
                    execution_mode,
                    capability.reason_for(network_profile),
                )
            try:
                argv = full_access_command(
                    [sys.executable, "-I", "-c", content],
                    working_directory=workspace,
                    network_profile=network_profile,
                )
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment_for_sandbox_launcher(),
                    cwd=workspace,
                )
            except (OSError, RuntimeError, SandboxUnavailable) as exc:
                return {
                    "error": f"python: {exc}",
                    "exit_code": 1,
                    "blocked": True,
                    "execution_mode": execution_mode.value,
                }
            stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
                proc,
                timeout=DEFAULT_PYTHON_TIMEOUT,
                progress_cb=progress_cb,
            )
            if timed_out:
                return {
                    "error": (
                        f"python: timed out after {DEFAULT_PYTHON_TIMEOUT}s — "
                        "process killed"
                    ),
                    "exit_code": 124,
                    "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                    "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                    "execution_mode": execution_mode.value,
                    "warning": FULL_ACCESS_WARNING,
                }
            setup_failure = _sandbox_setup_failure("python", stderr, rc)
            if setup_failure:
                setup_failure["execution_mode"] = execution_mode.value
                setup_failure["warning"] = FULL_ACCESS_WARNING
                return setup_failure
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (
                    (output + "\nSTDERR: " + err).strip()
                    if output
                    else "STDERR: " + err
                )
            return {
                "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
                "exit_code": rc or 0,
                "execution_mode": execution_mode.value,
                "network_enforcement": (
                    "brokered_http_https"
                    if network_profile is SandboxNetworkProfile.BROKERED_ONLY
                    else "networkless"
                ),
                "warning": FULL_ACCESS_WARNING,
            }

        if IS_WINDOWS:
            return blocked_process_result(
                "python",
                execution_mode,
                "Sandbox mode requires Linux with bubblewrap.",
            )
        capability = process_capability().sandbox
        if not capability.supports(network_profile):
            return blocked_process_result(
                "python",
                execution_mode,
                capability.reason_for(network_profile),
            )
        try:
            argv = sandbox_command(
                [sandbox_python_executable(), "-I", "-c", content],
                workspace=workspace,
                network_profile=network_profile,
            )
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment_for_sandbox_launcher(),
                cwd=workspace,
            )
        except (OSError, RuntimeError, SandboxUnavailable) as exc:
            return {
                "error": f"python: {exc}",
                "exit_code": 1,
                "blocked": True,
                "execution_mode": execution_mode.value,
            }
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc,
            timeout=DEFAULT_PYTHON_TIMEOUT,
            progress_cb=progress_cb,
        )
        if timed_out:
            return {
                "error": (
                    f"python: timed out after {DEFAULT_PYTHON_TIMEOUT}s — process killed"
                ),
                "exit_code": 124,
                "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                "execution_mode": execution_mode.value,
            }
        setup_failure = _sandbox_setup_failure("python", stderr, rc)
        if setup_failure:
            setup_failure["execution_mode"] = execution_mode.value
            return setup_failure
        output = stdout.rstrip()
        err = stderr.rstrip()
        if err:
            output = (
                (output + "\nSTDERR: " + err).strip()
                if output
                else "STDERR: " + err
            )
        return {
            "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": rc or 0,
            "execution_mode": execution_mode.value,
        }
