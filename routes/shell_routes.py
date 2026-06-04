"""Shell routes — user-facing command execution endpoint."""

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import uuid
import tempfile
from collections import namedtuple
from pathlib import Path
from typing import Dict, Any

# POSIX-only: `pty`/`fcntl` transitively import `termios`, which does NOT exist
# on Windows, so importing them unconditionally crashed app startup there
# (ModuleNotFoundError: termios — issues #140/#92/#63/#149/#150). The PTY code
# path is only reachable on POSIX; Windows uses pipe streaming + a detached-job
# fallback for the tmux feature (see _generate_win_detached).
try:
    import fcntl
    import pty
except ImportError as exc:
    fcntl = None
    pty = None
    _PTY_IMPORT_ERROR = exc
else:
    _PTY_IMPORT_ERROR = None

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.platform_compat import (
    IS_WINDOWS,
    detached_popen_kwargs,
    find_bash,
)


def _require_admin(request: Request):
    """Reject non-admin callers. Shell exec is admin-only — never expose to
    regular users; that's RCE-after-signup."""
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        # No auth at all — only safe in fully-trusted localhost dev mode
        return
    user = getattr(request.state, "current_user", None)
    # In-process tool loopback. The AuthMiddleware already validated the
    # internal token + loopback client before setting this marker, so
    # honour it here as admin-equivalent.
    if user == "internal-tool":
        return
    if not user or user == "api":
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")


def _reject_cross_site(request: Request):
    """Reject browser cross-site navigations to shell-touching endpoints."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected")


_SSH_PORT_RE = re.compile(r"^\d{1,5}$")
_SAFE_VENV_RE = re.compile(r"^[A-Za-z0-9_./~-]+$")


def _ssh_base_argv(host: str, ssh_port: str | None) -> list[str]:
    """Build an ssh argv prefix for remote probes without local-shell parsing."""
    if not host or not str(host).strip() or str(host).lstrip().startswith("-"):
        raise ValueError("invalid ssh host")
    argv = ["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no"]
    if ssh_port and str(ssh_port).strip() not in ("", "22"):
        port = str(ssh_port).strip()
        if not _SSH_PORT_RE.match(port) or not (1 <= int(port) <= 65535):
            raise ValueError("invalid ssh port")
        argv += ["-p", port]
    argv.append(str(host).strip())
    return argv


def _venv_activate_prefix(venv: str | None) -> str:
    """Return a remote activation prefix while preserving shell expansion of ~."""
    if not venv:
        return ""
    if not _SAFE_VENV_RE.match(venv):
        raise ValueError("invalid venv path")
    act = venv if venv.endswith("/bin/activate") else venv.rstrip("/") + "/bin/activate"
    return f". {act} && "

logger = logging.getLogger(__name__)

PTY_SUPPORTED = pty is not None and fcntl is not None and hasattr(os, "setsid")


DOCKER_IN_CONTAINER_HINT = (
    "Not available inside the Odysseus container by design. The image ships no "
    "docker CLI and no host socket is mounted. Run Docker-backed launches on a "
    "remote server, where docker is checked over SSH. Mounting /var/run/docker.sock "
    "into the container would grant it host-root access, so only do that if you "
    "accept that risk."
)


def _running_in_container(dockerenv_path="/.dockerenv", cgroup_path="/proc/1/cgroup"):
    if os.path.exists(dockerenv_path):
        return True
    try:
        with open(cgroup_path, "r", encoding="utf-8") as fh:
            contents = fh.read()
    except OSError:
        return False
    return any(token in contents for token in ("docker", "containerd", "kubepods"))


DockerRowStatus = namedtuple("DockerRowStatus", ["applicable", "install_hint"])
PackageUpdateStatus = namedtuple("PackageUpdateStatus", ["available", "note"])


def _docker_row_status(*, on_remote, in_container, installed, default_hint):
    local_docker_unavailable = not on_remote and in_container and not installed
    if local_docker_unavailable:
        return DockerRowStatus(applicable=False, install_hint=DOCKER_IN_CONTAINER_HINT)
    return DockerRowStatus(applicable=True, install_hint=default_hint)


def _pip_dist_name(pkg: dict) -> str:
    """Distribution name for importlib.metadata lookups.

    The Cookbook package catalog carries both the import name (``name``, e.g.
    ``llama_cpp``) and the pip spec (``pip``, e.g. ``llama-cpp-python[server]``).
    The distribution is NOT always the import name with underscores swapped for
    dashes — ``llama_cpp`` ships in the ``llama-cpp-python`` distribution — so
    derive it from the pip spec (stripping any ``[extras]`` and version markers)
    and fall back to the munged import name only when no pip spec is declared.
    """
    pip = (pkg.get("pip") or "").strip()
    if pip:
        base = re.split(r"[\[<>=!~;\s]", pip, maxsplit=1)[0].strip()
        if base:
            return base
    return (pkg.get("name") or "").replace("_", "-")


def _package_installed_from_probe(name: str, probe: dict) -> bool:
    """Return whether an optional dependency is usable by Cookbook.

    A Python import alone is not enough: namespace packages can be created by a
    same-named directory, and vLLM serving needs the CLI on PATH. Keep this
    aligned with the actual serve command each backend launches.
    """
    binaries = probe.get("binaries") if isinstance(probe.get("binaries"), dict) else {}
    dists = probe.get("dists") if isinstance(probe.get("dists"), dict) else {}
    modules = probe.get("modules") if isinstance(probe.get("modules"), dict) else {}

    if name == "vllm":
        return bool(binaries.get("vllm"))
    if name == "llama_cpp":
        return bool(binaries.get("llama-server") or dists.get("llama-cpp-python"))
    if name == "sglang":
        return bool(dists.get("sglang") or modules.get("sglang", {}).get("real_module"))
    if name == "diffusers":
        return bool(
            (dists.get("diffusers") or modules.get("diffusers", {}).get("real_module"))
            and (dists.get("torch") or modules.get("torch", {}).get("real_module"))
        )
    if name == "hf_transfer":
        return bool(dists.get("hf-transfer") or modules.get("hf_transfer", {}).get("real_module"))
    return bool(dists.get(name) or modules.get(name, {}).get("real_module"))


def _package_status_note(name: str, probe: dict) -> str:
    binaries = probe.get("binaries") if isinstance(probe.get("binaries"), dict) else {}
    modules = probe.get("modules") if isinstance(probe.get("modules"), dict) else {}
    dists = probe.get("dists") if isinstance(probe.get("dists"), dict) else {}
    module = modules.get(name) if isinstance(modules.get(name), dict) else {}
    locations = module.get("locations") or []
    if name == "vllm":
        if binaries.get("vllm"):
            parts = [f"vLLM CLI: {binaries['vllm']}"]
            if dists.get("vllm"):
                parts.append(f"python package: vllm {dists['vllm']}")
            return "; ".join(parts)
        if module.get("found") and not dists.get("vllm"):
            loc = locations[0] if locations else module.get("origin") or "unknown path"
            return f"Python sees a vllm namespace at {loc}, but no vLLM CLI is on PATH."
        return "vLLM CLI not found on PATH."
    if name == "llama_cpp":
        parts = []
        if binaries.get("llama-server"):
            parts.append(f"native llama-server: {binaries['llama-server']}")
        if dists.get("llama-cpp-python"):
            parts.append(f"python package: llama-cpp-python {dists['llama-cpp-python']}")
        return "; ".join(parts) if parts else "No native llama-server or llama-cpp-python server package found."
    if name == "diffusers":
        if _package_installed_from_probe(name, probe):
            return f"diffusers {dists.get('diffusers', 'available')} with torch {dists.get('torch', 'available')}"
        return "Diffusers serving needs both diffusers and torch."
    if name in dists:
        return f"{name} {dists[name]}"
    return ""


def _package_pip_update_status(pkg: dict, probe: dict | None = None) -> PackageUpdateStatus:
    """Return whether the Dependencies UI should offer a generic pip update.

    "Installed" means Cookbook can use the dependency. It does not always mean
    the dependency is a Python package that Cookbook should update with pip:
    native llama-server can come from a package manager/source build, and a CLI
    may be on PATH without matching Python package metadata.
    """
    if pkg.get("kind") == "system" or not pkg.get("pip"):
        return PackageUpdateStatus(False, "Update this system dependency outside Odysseus.")

    name = pkg.get("name")
    binaries = probe.get("binaries") if isinstance(probe, dict) and isinstance(probe.get("binaries"), dict) else {}
    dists = probe.get("dists") if isinstance(probe, dict) and isinstance(probe.get("dists"), dict) else {}

    if name == "llama_cpp" and binaries.get("llama-server"):
        return PackageUpdateStatus(
            False,
            "Using native llama-server on PATH; update it with its package manager or source checkout.",
        )
    if name == "vllm" and binaries.get("vllm") and not dists.get("vllm"):
        return PackageUpdateStatus(
            False,
            "Using a vLLM CLI on PATH without Python package metadata; update it outside Odysseus.",
        )

    return PackageUpdateStatus(True, "Update uses pip in the selected Python environment.")


def _prepend_user_install_bins_to_path() -> None:
    """Make pip --user console scripts visible to dependency probes.

    Docker Cookbook installs vLLM with `python -m pip install --user`, which
    drops the `vllm` CLI in /app/.local/bin. The running app process does not
    inherit that PATH update, so `shutil.which("vllm")` can report missing even
    after a successful install.
    """
    try:
        import site

        candidates = [os.path.join(site.USER_BASE, "bin")]
    except Exception:
        candidates = []
    candidates.append(os.path.expanduser("~/.local/bin"))

    parts = os.environ.get("PATH", "").split(os.pathsep) if os.environ.get("PATH") else []
    changed = False
    for path in reversed([p for p in candidates if p]):
        if path not in parts:
            parts.insert(0, path)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


def _package_probe_script(names: list[str]) -> str:
    names_lit = ",".join(repr(n) for n in names)
    return f"""
import importlib.util
import importlib.metadata as md
import json
import os
import shutil
import site

names=[{names_lit}]
dist_names={{
    'vllm':['vllm'],
    'llama_cpp':['llama-cpp-python'],
    'sglang':['sglang'],
    'diffusers':['diffusers','torch'],
    'hf_transfer':['hf-transfer','hf_transfer'],
}}
bin_names={{
    'vllm':['vllm'],
    'llama_cpp':['llama-server'],
}}

def add_user_install_bins_to_path():
    candidates = []
    try:
        candidates.append(os.path.join(site.USER_BASE, 'bin'))
    except Exception:
        pass
    candidates.append(os.path.expanduser('~/.local/bin'))
    parts = os.environ.get('PATH', '').split(os.pathsep) if os.environ.get('PATH') else []
    changed = False
    for path in reversed([p for p in candidates if p]):
        if path not in parts:
            parts.insert(0, path)
            changed = True
    if changed:
        os.environ['PATH'] = os.pathsep.join(parts)

add_user_install_bins_to_path()

def mod_status(n):
    spec = importlib.util.find_spec(n)
    loader = getattr(spec, 'loader', None) if spec else None
    return {{
        'found': bool(spec),
        'origin': getattr(spec, 'origin', None) if spec else None,
        'loader': type(loader).__name__ if loader else None,
        'locations': list(getattr(spec, 'submodule_search_locations', []) or []),
        'real_module': bool(spec and loader),
    }}

def dist_status(ds):
    out = {{}}
    for d in ds:
        try:
            out[d] = md.version(d)
        except Exception:
            pass
    return out

def probe(n):
    mods = {{n: mod_status(n)}}
    if n == 'diffusers':
        mods['torch'] = mod_status('torch')
    dists = dist_status(dist_names.get(n, [n]))
    bins = {{b: shutil.which(b) for b in bin_names.get(n, [])}}
    return {{'modules': mods, 'dists': dists, 'binaries': bins}}

print(json.dumps({{n: probe(n) for n in names}}))
"""


def _find_line_break(buf):
    """Find next line terminator in buffer. Returns (index, separator_length) or (-1, 0)."""
    ni = buf.find(b"\n")
    ri = buf.find(b"\r")
    if ni == -1 and ri == -1:
        return -1, 0
    if ni == -1:
        return ri, 1
    if ri == -1:
        return ni, 1
    if ri < ni:
        return ri, (2 if ri + 1 == ni else 1)
    return ni, 1


EXEC_TIMEOUT = 30  # seconds — shorter than agent's 60s
STREAM_TIMEOUT = 120  # default for short commands
MAX_OUTPUT = 200_000  # truncate limit
TMUX_LOG_DIR = Path(tempfile.gettempdir()) / "odysseus-tmux"
PTY_UNSUPPORTED_ERROR = "pty_unsupported"


class ShellExecRequest(BaseModel):
    command: str
    timeout: int | None = None  # optional override; 0 = no timeout (run until client disconnects)
    use_pty: bool = False       # use pseudo-TTY (for progress bars)
    use_tmux: bool = False      # run in tmux session (survives browser disconnect)


async def _create_shell(command: str, **kwargs):
    """Spawn a shell subprocess for `command`.

    POSIX: /bin/sh via create_subprocess_shell (unchanged behaviour).
    Windows: prefer a real bash (Git Bash/WSL) so bash-syntax commands behave
    the same as on Linux; fall back to cmd.exe when no bash is installed.
    """
    if IS_WINDOWS:
        bash = find_bash()
        if bash:
            return await asyncio.create_subprocess_exec(bash, "-c", command, **kwargs)
    return await asyncio.create_subprocess_shell(command, **kwargs)


async def _exec_shell(command: str, timeout: int = EXEC_TIMEOUT) -> Dict[str, Any]:
    """Run a shell command and return stdout/stderr/exit_code."""
    proc = None
    try:
        proc = await _create_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        stdout = stdout_b.decode(errors="replace")[:MAX_OUTPUT]
        stderr = stderr_b.decode(errors="replace")[:MAX_OUTPUT]
        return {"stdout": stdout, "stderr": stderr, "exit_code": proc.returncode}
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        return {"stdout": "", "stderr": f"Command timed out after {timeout}s", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


async def _generate_pty(cmd: str, timeout: int, request: Request):
    """Run command in a pseudo-TTY so tqdm/progress bars work natively."""
    if not PTY_SUPPORTED:
        msg = "PTY streaming is not supported on this platform"
        if _PTY_IMPORT_ERROR:
            msg += f": {_PTY_IMPORT_ERROR}"
        yield f"data: {json.dumps({'stream': 'stderr', 'data': msg, 'error': PTY_UNSUPPORTED_ERROR})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1, 'error': PTY_UNSUPPORTED_ERROR})}\n\n"
        return

    loop = asyncio.get_running_loop()
    master_fd, slave_fd = pty.openpty()

    # Set master to non-blocking
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(Path.home()),
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)  # parent doesn't need the slave side

    deadline = (loop.time() + timeout) if timeout else None
    buf = b""
    process_done = asyncio.Event()

    async def _wait_proc():
        await proc.wait()
        process_done.set()

    wait_task = asyncio.create_task(_wait_proc())

    try:
        while not process_done.is_set():
            if deadline and loop.time() > deadline:
                proc.kill()
                await proc.wait()
                yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Command timed out after {timeout}s'})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
                return

            # Check client disconnect
            if await request.is_disconnected():
                proc.kill()
                await proc.wait()
                return

            # Read available data from PTY
            try:
                chunk = await asyncio.wait_for(
                    loop.run_in_executor(None, _pty_read, master_fd),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                continue
            except OSError:
                break

            if chunk is None:
                # No data yet, keep waiting
                continue
            if chunk == b"":
                # EOF — process closed the PTY
                break

            buf += chunk
            # Split on \r or \n
            while True:
                idx, sep_len = _find_line_break(buf)
                if idx == -1:
                    break
                line = buf[:idx].decode(errors="replace")
                buf = buf[idx + sep_len:]
                if line:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"

        # Drain any remaining PTY output after process exits
        try:
            while True:
                rest = _pty_read(master_fd)
                if rest is None or rest == b"":
                    break
                buf += rest
        except OSError:
            pass

        # Flush remaining buffer
        if buf:
            # Split remaining buffer same as above
            while True:
                idx, sep_len = _find_line_break(buf)
                if idx == -1:
                    break
                line = buf[:idx].decode(errors="replace")
                buf = buf[idx + sep_len:]
                if line:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
            if buf:
                text = buf.decode(errors="replace").strip()
                if text:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': text})}\n\n"

        await wait_task
        yield f"data: {json.dumps({'exit_code': proc.returncode})}\n\n"

    except Exception as e:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        yield f"data: {json.dumps({'stream': 'stderr', 'data': str(e)})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
    finally:
        wait_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass


def _pty_read(fd: int) -> bytes | None:
    """Blocking read from PTY fd. Called via run_in_executor.
    Returns bytes on data, None on timeout (no data yet)."""
    import select
    r, _, _ = select.select([fd], [], [], 1.0)
    if r:
        try:
            data = os.read(fd, 4096)
            return data if data else b""  # empty = EOF
        except OSError:
            return b""  # fd closed = EOF
    return None  # timeout, no data yet


async def _generate_tmux(cmd: str, request: Request):
    """Run command in a tmux session. Streams output via a log file.
    The tmux session survives browser disconnect — user can reconnect or
    `tmux attach -t <name>` to see it live."""
    TMUX_LOG_DIR.mkdir(parents=True, exist_ok=True)
    session_id = f"cookbook-{uuid.uuid4().hex[:8]}"
    log_path = TMUX_LOG_DIR / f"{session_id}.log"

    # Write a wrapper script that runs the command, tees output, and records exit code.
    # Using a script avoids shell quoting issues with the tmux command.
    script_path = TMUX_LOG_DIR / f"{session_id}.sh"
    script_path.write_text(
        f"#!/bin/bash\n"
        f"ODYSSEUS_USER_SHELL=\"${{SHELL:-}}\"\n"
        f"if [ -n \"$ODYSSEUS_USER_SHELL\" ] && [ -x \"$ODYSSEUS_USER_SHELL\" ]; then\n"
        f"  ODYSSEUS_USER_PATH=\"$(\"$ODYSSEUS_USER_SHELL\" -ic 'printf \"__ODYSSEUS_PATH__%s\\n\" \"$PATH\"' 2>/dev/null | sed -n 's/^__ODYSSEUS_PATH__//p' | tail -n 1 || true)\"\n"
        f"  if [ -n \"$ODYSSEUS_USER_PATH\" ]; then export PATH=\"$ODYSSEUS_USER_PATH:$PATH\"; fi\n"
        f"fi\n"
        f"{cmd} 2>&1 | tee '{log_path}'\n"
        f"EC=${{PIPESTATUS[0]}}\n"
        f"echo ':::EXIT_CODE:::'$EC >> '{log_path}'\n"
        f"rm -f '{script_path}'\n"
        f"exit $EC\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    logger.info("tmux wrapper script created: session=%s path=%s", session_id, script_path)

    tmux_cmd = f"tmux new-session -d -s {session_id} {shlex.quote(str(script_path))}"

    proc = await asyncio.create_subprocess_shell(
        tmux_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode(errors="replace")
        yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Failed to start tmux: {stderr}'})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
        return

    yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Started tmux session: {session_id}'})}\n\n"

    # Tail the log file, streaming new lines as SSE
    lines_sent = 0
    exit_code = None

    while True:
        # Check client disconnect
        if await request.is_disconnected():
            # tmux keeps running — that's the whole point
            yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Disconnected. tmux session {session_id} continues in background.'})}\n\n"
            return

        # Read new lines from log
        try:
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                new_lines = lines[lines_sent:]
                for line in new_lines:
                    if line.startswith(":::EXIT_CODE:::"):
                        try:
                            exit_code = int(line.split(":::")[-1])
                        except ValueError:
                            exit_code = -1
                    else:
                        yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
                lines_sent = len(lines)
        except Exception as e:
            logger.debug(f"tmux log read error: {e}")

        if exit_code is not None:
            break

        # Check if tmux session is still alive
        check = await asyncio.create_subprocess_shell(
            f"tmux has-session -t {session_id} 2>/dev/null",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await check.wait()
        if check.returncode != 0:
            # Session ended — do one final read
            await asyncio.sleep(0.5)
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[lines_sent:]:
                    if line.startswith(":::EXIT_CODE:::"):
                        try:
                            exit_code = int(line.split(":::")[-1])
                        except ValueError:
                            exit_code = -1
                    else:
                        yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
            if exit_code is None:
                exit_code = 0
            break

        await asyncio.sleep(1.0)

    yield f"data: {json.dumps({'exit_code': exit_code})}\n\n"

    # Clean up log file
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass


async def _generate_win_detached(cmd: str, request: Request):
    """Windows stand-in for the tmux path (issues #84/#162).

    tmux doesn't exist on Windows, so we run the command in a *detached* child
    (DETACHED_PROCESS — survives browser disconnect, same as the tmux session)
    that writes output to a log file, and tail that log over SSE. Prefers bash
    (Git Bash) for command-syntax parity; falls back to cmd.exe. There's no
    `tmux attach` equivalent, but the "keeps running if you disconnect" contract
    holds, which is the point of the feature for long Cookbook downloads."""
    TMUX_LOG_DIR.mkdir(parents=True, exist_ok=True)
    session_id = f"cookbook-{uuid.uuid4().hex[:8]}"
    log_path = TMUX_LOG_DIR / f"{session_id}.log"
    exit_path = TMUX_LOG_DIR / f"{session_id}.exit"

    bash = find_bash()
    if bash:
        script_path = TMUX_LOG_DIR / f"{session_id}.sh"
        script_path.write_text(
            f"{cmd} > {shlex.quote(str(log_path))} 2>&1\n"
            f"echo $? > {shlex.quote(str(exit_path))}\n",
            encoding="utf-8",
        )
        argv = [bash, str(script_path)]
    else:
        script_path = TMUX_LOG_DIR / f"{session_id}.cmd"
        # cmd.exe wrapper: run, redirect all output to the log, record exit code.
        script_path.write_text(
            "@echo off\r\n"
            f'call {cmd} > "{log_path}" 2>&1\r\n'
            f'echo %ERRORLEVEL%> "{exit_path}"\r\n',
            encoding="utf-8",
        )
        argv = [os.environ.get("ComSpec", "cmd.exe"), "/c", str(script_path)]

    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **detached_popen_kwargs(),
        )
    except Exception as e:
        yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Failed to launch background job: {e}'})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
        return

    yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Started background job: {session_id}'})}\n\n"

    lines_sent = 0
    exit_code = None
    while True:
        if await request.is_disconnected():
            yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Disconnected. Background job {session_id} continues running.'})}\n\n"
            return
        try:
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[lines_sent:]:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
                lines_sent = len(lines)
        except Exception as e:
            logger.debug("win detached log read error: %s", e)

        if exit_path.exists():
            # Drain any final lines, then read the recorded exit code.
            await asyncio.sleep(0.3)
            try:
                if log_path.exists():
                    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines[lines_sent:]:
                        yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
                    lines_sent = len(lines)
                exit_code = int((exit_path.read_text(encoding="utf-8", errors="replace").strip() or "0"))
            except Exception:
                exit_code = 0
            break
        await asyncio.sleep(1.0)

    yield f"data: {json.dumps({'exit_code': exit_code})}\n\n"
    for p in (log_path, exit_path, script_path):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def setup_shell_routes() -> APIRouter:
    router = APIRouter(tags=["shell"])

    @router.post("/api/shell/exec")
    async def shell_exec(request: Request, req: ShellExecRequest) -> Dict[str, Any]:
        """Execute a shell command and return output. Admin only."""
        _require_admin(request)
        cmd = req.command.strip()
        if not cmd:
            return {"stdout": "", "stderr": "No command provided", "exit_code": 1}

        logger.info("User shell exec requested: length=%d", len(cmd))
        result = await _exec_shell(cmd, timeout=EXEC_TIMEOUT)
        return result

    @router.post("/api/shell/stream")
    async def shell_stream(request: Request, req: ShellExecRequest):
        """Execute a shell command and stream output line-by-line via SSE. Admin only."""
        _require_admin(request)
        cmd = req.command.strip()
        if not cmd:
            async def empty():
                yield f"data: {json.dumps({'stream': 'stderr', 'data': 'No command provided'})}\n\n"
                yield f"data: {json.dumps({'exit_code': 1})}\n\n"
            return StreamingResponse(empty(), media_type="text/event-stream")

        timeout = req.timeout if req.timeout is not None else STREAM_TIMEOUT
        use_pty = req.use_pty
        use_tmux = req.use_tmux
        logger.info(
            "User shell stream requested: timeout=%s pty=%s tmux=%s length=%d",
            "none" if timeout == 0 else f"{timeout}s",
            use_pty,
            use_tmux,
            len(cmd),
        )

        if use_tmux:
            # tmux is POSIX-only; Windows uses a detached-process + logfile tail
            # that preserves the "survives disconnect" behaviour.
            gen = _generate_win_detached(cmd, request) if IS_WINDOWS else _generate_tmux(cmd, request)
            return StreamingResponse(gen, media_type="text/event-stream")

        if use_pty and not IS_WINDOWS:
            return StreamingResponse(
                _generate_pty(cmd, timeout, request),
                media_type="text/event-stream",
            )
        # Windows has no PTY; fall through to pipe streaming below (output still
        # streams line-by-line, just without live in-place progress-bar redraws).

        async def generate():
            proc = None
            reader_tasks = []
            try:
                proc = await _create_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(Path.home()),
                )

                q: asyncio.Queue = asyncio.Queue()

                async def _reader(stream, name):
                    """Read chunks, split on \\n or \\r for progress bar support."""
                    try:
                        buf = b""
                        while True:
                            chunk = await stream.read(4096)
                            if not chunk:
                                if buf:
                                    await q.put((name, buf.decode(errors="replace").rstrip("\r\n")))
                                break
                            buf += chunk
                            while True:
                                idx, sep_len = _find_line_break(buf)
                                if idx == -1:
                                    break
                                line = buf[:idx].decode(errors="replace")
                                buf = buf[idx + sep_len:]
                                if line:
                                    await q.put((name, line))
                    finally:
                        await q.put((name, None))

                reader_tasks = [
                    asyncio.create_task(_reader(proc.stdout, "stdout")),
                    asyncio.create_task(_reader(proc.stderr, "stderr")),
                ]

                finished = 0
                loop = asyncio.get_running_loop()
                deadline = (loop.time() + timeout) if timeout else None
                while finished < 2:
                    if deadline:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError()
                        wait = min(remaining, 2.0)
                    else:
                        wait = 2.0

                    try:
                        name, text = await asyncio.wait_for(q.get(), timeout=wait)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            if proc:
                                proc.kill()
                            return
                        continue

                    if text is None:
                        finished += 1
                        continue
                    yield f"data: {json.dumps({'stream': name, 'data': text})}\n\n"

                await proc.wait()
                yield f"data: {json.dumps({'exit_code': proc.returncode})}\n\n"

            except asyncio.TimeoutError:
                if proc:
                    try:
                        proc.kill()
                        await proc.wait()
                    except ProcessLookupError:
                        pass
                yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Command timed out after {timeout}s'})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'stream': 'stderr', 'data': str(e)})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
            finally:
                for t in reader_tasks:
                    t.cancel()

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/api/cookbook/packages")
    async def list_packages(request: Request, host: str | None = None, ssh_port: str | None = None, venv: str | None = None):
        """Check which optional packages are installed.

        Local-target packages are checked in-process. Remote-target packages
        (vllm, sglang, llama_cpp, diffusers, hf_transfer) are checked on the SELECTED
        server over SSH, inside its venv — otherwise installing on a remote box
        never reflected because the check only ever looked at the local host.
        """
        _require_admin(request)
        _reject_cross_site(request)
        import importlib, importlib.metadata as importlib_metadata, shlex, json as _json, site, sys
        _prepend_user_install_bins_to_path()
        importlib.invalidate_caches()
        try:
            user_site = site.getusersitepackages()
            if user_site and os.path.isdir(user_site) and user_site not in sys.path:
                sys.path.append(user_site)
        except Exception:
            pass
        if ssh_port and str(ssh_port).strip() not in ("", "22"):
            _port = str(ssh_port).strip()
            if not _SSH_PORT_RE.match(_port) or not (1 <= int(_port) <= 65535):
                raise HTTPException(400, "Invalid ssh_port")
        packages = [
            # ── System ── OS binaries, not pip packages
            {"name": "tmux", "pip": "", "desc": "Required for Linux/Termux Cookbook background downloads and serves", "category": "System", "target": "remote", "kind": "system", "install_hint": "Run Cookbook server setup, or install tmux with apt/pacman/dnf/apk/zypper."},
            {"name": "docker", "pip": "", "desc": "Required only for Docker-backed launch commands", "category": "System", "target": "remote", "kind": "system", "install_hint": "Install Docker on the selected server and allow this user to run docker."},
            # ── LLM ── installs on GPU servers for model serving/downloading
            {"name": "hf_transfer", "pip": "hf_transfer", "desc": "Fast model downloads from HuggingFace", "category": "LLM", "target": "remote"},
            {"name": "llama_cpp", "pip": "llama-cpp-python[server]", "desc": "Serve GGUF models via llama.cpp", "category": "LLM", "target": "remote"},
            {"name": "sglang", "pip": "sglang[all]", "desc": "Serve HF safetensors models via SGLang", "category": "LLM", "target": "remote"},
            {"name": "vllm", "pip": "vllm", "desc": "High-throughput LLM serving engine", "category": "LLM", "target": "remote"},
            # ── Image ── editor + diffusion model serving
            {"name": "diffusers", "pip": "diffusers[torch]", "desc": "Image generation pipelines (SD, Flux) with PyTorch", "category": "Image", "target": "remote"},
            {"name": "rembg", "pip": "rembg[gpu]", "desc": "AI background removal for image editor", "category": "Image", "target": "local"},
            {"name": "realesrgan", "pip": "realesrgan", "desc": "AI denoise + upscale (Real-ESRGAN). Used by editor's Denoise and Upscale tools.", "category": "Image", "target": "local"},
            # ── Tools ──
            {"name": "playwright", "pip": "playwright", "desc": "Browser automation for web tools", "category": "Tools", "target": "local"},
        ]
        # Remote check: for remote-target packages, probe the selected server's
        # venv over SSH so a remote `pip install` actually reflects here.
        remote_status: dict = {}
        remote_details: dict = {}
        remote_names = [p["name"] for p in packages if p.get("target") == "remote" and p.get("kind") != "system"]
        remote_system_names = [p["name"] for p in packages if p.get("target") == "remote" and p.get("kind") == "system"]
        if host and remote_names:
            try:
                py = _package_probe_script(remote_names)
                # `venv` is validated but left unquoted so leading ~ expands on
                # the remote; quoting it breaks ~/venv activation.
                src = _venv_activate_prefix(venv)
                inner = f"{src}python3 -c {shlex.quote(py)}"
                argv = _ssh_base_argv(host, ssh_port) + [inner]
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=12)
                txt = out.decode("utf-8", errors="replace").strip()
                # The activate script can emit noise — take the last JSON line.
                for line in reversed(txt.splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        remote_details = _json.loads(line)
                        remote_status = {
                            name: _package_installed_from_probe(name, probe)
                            for name, probe in remote_details.items()
                            if isinstance(probe, dict)
                        }
                        break
            except ValueError as e:
                raise HTTPException(400, str(e))
            except Exception:
                remote_status = {}
        if host and remote_system_names:
            try:
                checks = []
                for name in remote_system_names:
                    qn = shlex.quote(name)
                    checks.append(f"if command -v {qn} >/dev/null 2>&1; then echo {qn}=1; else echo {qn}=0; fi")
                inner = " ; ".join(checks)
                argv = _ssh_base_argv(host, ssh_port) + [inner]
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=12)
                txt = out.decode("utf-8", errors="replace").strip()
                for line in txt.splitlines():
                    name, sep, value = line.strip().partition("=")
                    if sep and name in remote_system_names:
                        remote_status[name] = value == "1"
            except ValueError as e:
                raise HTTPException(400, str(e))
            except Exception:
                pass

        for pkg in packages:
            on_remote = bool(host and pkg.get("target") == "remote")
            probe = None
            if on_remote:
                pkg["installed"] = bool(remote_status.get(pkg["name"], False))
                probe = remote_details.get(pkg["name"])
                if isinstance(probe, dict):
                    pkg["details"] = probe
                    note = _package_status_note(pkg["name"], probe)
                    if note:
                        pkg["status_note"] = note
            elif pkg.get("kind") == "system":
                pkg["installed"] = shutil.which(pkg["name"]) is not None
            elif pkg["name"] == "llama_cpp" and shutil.which("llama-server"):
                pkg["installed"] = True
                pkg["status_note"] = f"native llama-server: {shutil.which('llama-server')}"
                probe = {"binaries": {"llama-server": shutil.which("llama-server")}, "dists": {}}
            elif pkg["name"] == "vllm":
                _vllm_cli = shutil.which("vllm")
                pkg["installed"] = _vllm_cli is not None
                if pkg["installed"]:
                    try:
                        _vllm_version = importlib_metadata.version(_pip_dist_name(pkg))
                    except importlib_metadata.PackageNotFoundError:
                        _vllm_version = None
                    probe = {
                        "binaries": {"vllm": _vllm_cli},
                        "dists": {"vllm": _vllm_version} if _vllm_version else {},
                    }
                    pkg["status_note"] = _package_status_note("vllm", probe)
            else:
                try:
                    importlib.import_module(pkg["name"])
                    importlib_metadata.version(_pip_dist_name(pkg))
                    pkg["installed"] = True
                except ImportError:
                    pkg["installed"] = False
                except importlib_metadata.PackageNotFoundError:
                    pkg["installed"] = False

            if pkg.get("installed"):
                update_status = _package_pip_update_status(pkg, probe)
                pkg["pip_update_available"] = update_status.available
                if update_status.note:
                    pkg["update_note"] = update_status.note

            if pkg["name"] == "docker":
                status = _docker_row_status(
                    on_remote=on_remote,
                    in_container=_running_in_container() if not on_remote else False,
                    installed=pkg["installed"],
                    default_hint=pkg.get("install_hint"),
                )
                pkg["applicable"] = status.applicable
                pkg["install_hint"] = status.install_hint
        return {"packages": packages}

    @router.post("/api/cookbook/packages/install")
    async def install_package(request: Request):
        """Install a package via pip. Admin only — pip install is effectively code exec."""
        _require_admin(request)
        import sys as _sys
        body = await request.json()
        pip_name = body.get("pip")
        if not pip_name:
            return {"ok": False, "error": "No package specified"}
        # Validate against known packages to prevent arbitrary pip install
        known = {
            "rembg[gpu]", "hf_transfer", "llama-cpp-python[server]", "sglang[all]", "diffusers", "diffusers[torch]",
            "TTS", "bark", "faster-whisper", "playwright", "realesrgan", "gfpgan",
            "insightface", "onnxruntime-gpu", "onnxruntime", "hdbscan", "vllm",
        }
        if pip_name not in known:
            return {"ok": False, "error": f"Unknown package: {pip_name}"}
        cmd = [_sys.executable, "-m", "pip", "install", pip_name]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return {"ok": True, "output": stdout.decode()[-200:]}
        return {"ok": False, "error": stderr.decode()[-300:]}

    @router.post("/api/cookbook/rebuild-engine")
    async def rebuild_engine(request: Request):
        """Clear the cached llama.cpp build so the next serve recompiles.

        Admin only — this removes the Cookbook-managed ``~/bin/llama-server``
        symlink and ``~/llama.cpp/build`` directory, locally or on the selected
        remote server. It installs and downloads nothing; the next llama.cpp
        serve rebuilds from source and picks up CUDA/HIP if a toolchain is now
        present. This is the missing "force a fresh GPU build" lever for hosts
        stuck on a CPU-only llama-server.
        """
        _require_admin(request)
        from routes.cookbook_helpers import _llama_cpp_rebuild_cmd
        body = await request.json()
        engine = str(body.get("engine") or "llamacpp").strip()
        if engine != "llamacpp":
            return {"ok": False, "error": f"Unsupported engine: {engine}"}
        host = str(body.get("remote_host") or "").strip()
        ssh_port = body.get("ssh_port")
        cmd = _llama_cpp_rebuild_cmd()
        try:
            argv = (_ssh_base_argv(host, ssh_port) + [cmd]) if host else ["bash", "-lc", cmd]
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Rebuild-engine command timed out."}
        if proc.returncode == 0:
            return {"ok": True, "output": out.decode("utf-8", errors="replace")[-400:]}
        return {"ok": False, "error": err.decode("utf-8", errors="replace")[-400:]}

    # ── Shell filesystem endpoints ────────────────────────────────────────────
    # Both endpoints are restricted to the user's home directory. Paths outside
    # that root are rejected with 403 — no traversal is possible.

    _HOME = Path(os.path.expanduser("~")).resolve()

    _TEXT_EXTS = {
        '.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs',
        '.html', '.htm', '.css', '.scss', '.less',
        '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env',
        '.md', '.txt', '.rst', '.csv', '.log', '.gitignore', '.gitattributes',
        '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
        '.sql', '.r', '.rb', '.go', '.rs', '.java', '.c', '.cpp', '.h', '.hpp',
        '.xml', '.svg', '.dockerfile', '.makefile', '.lock', '',
    }
    _MAX_FILE_BYTES = 500_000  # 500 KB hard cap

    def _safe_path(raw: str) -> Path:
        """Resolve *raw* and assert it sits inside _HOME. Raises HTTPException on violation."""
        try:
            p = Path(raw).resolve()
        except Exception:
            raise HTTPException(400, "Invalid path")
        try:
            p.relative_to(_HOME)
        except ValueError:
            raise HTTPException(403, f"Path must be inside {_HOME}")
        return p

    @router.get("/api/shell/browse")
    async def shell_browse(request: Request, path: str = ""):
        """List directory contents — folders then files, sorted alphabetically.
        Restricted to the user's home directory. Admin only."""
        _require_admin(request)
        _reject_cross_site(request)
        target = _safe_path(path) if path else _HOME
        if not target.exists() or not target.is_dir():
            raise HTTPException(404, "Directory not found")

        dirs, files = [], []
        try:
            for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.name.startswith('.'):
                    continue
                try:
                    if item.is_dir():
                        dirs.append({"name": item.name, "path": str(item)})
                    elif item.is_file():
                        files.append({
                            "name": item.name,
                            "path": str(item),
                            "size": item.stat().st_size,
                            "ext": item.suffix.lower(),
                        })
                except OSError:
                    pass
        except PermissionError:
            pass

        parent = str(target.parent) if target != _HOME else None
        return {"path": str(target), "root": str(_HOME), "parent": parent, "dirs": dirs, "files": files}

    @router.get("/api/shell/readfile")
    async def shell_readfile(request: Request, path: str):
        """Read a text file from the local filesystem. 500 KB limit. Admin only."""
        _require_admin(request)
        _reject_cross_site(request)
        target = _safe_path(path)
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "File not found")
        if target.suffix.lower() not in _TEXT_EXTS:
            raise HTTPException(400, "Binary file — cannot display")
        size = target.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise HTTPException(400, f"File too large ({size // 1024} KB > 500 KB)")
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(500, str(exc))
        return {"path": str(target), "name": target.name, "ext": target.suffix.lower(), "content": content}

    # ── Git integration endpoints ─────────────────────────────────────────────

    class _GitFileReq(BaseModel):
        path: str       # repo root (abs path inside _HOME)
        file: str = ""  # relative file; empty = all files

    class _GitCommitReq(BaseModel):
        path: str
        message: str

    async def _git_run(*args, cwd: str, timeout: int = 10):
        """Run a git sub-command, return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode("utf-8", errors="replace"), \
               stderr.decode("utf-8", errors="replace")

    @router.get("/api/shell/gitstatus")
    async def shell_gitstatus(request: Request, path: str = ""):
        """git status --porcelain for the given directory. Admin only."""
        _require_admin(request)
        _reject_cross_site(request)
        target = _safe_path(path) if path else _HOME
        try:
            rc, stdout, _ = await _git_run("status", "--porcelain", "-uall",
                                           cwd=str(target))
            if rc == 128:
                return {"is_git": False, "files": [], "cwd": str(target)}
            files = []
            for line in stdout.splitlines():
                if len(line) < 3:
                    continue
                xy = line[:2]
                fname = line[3:]
                if " -> " in fname:          # renamed: "old -> new"
                    fname = fname.split(" -> ", 1)[1]
                files.append({"xy": xy, "file": fname.strip()})
            return {"is_git": True, "files": files, "cwd": str(target)}
        except asyncio.TimeoutError:
            return {"is_git": False, "files": [], "error": "git timed out", "cwd": str(target)}
        except Exception as exc:
            return {"is_git": False, "files": [], "error": str(exc), "cwd": str(target)}

    @router.get("/api/shell/gitdiff")
    async def shell_gitdiff(request: Request, path: str, file: str,
                            staged: bool = False):
        """git diff for a single file. Admin only."""
        _require_admin(request)
        _reject_cross_site(request)
        repo = _safe_path(path)
        # Verify the file path stays inside _HOME
        file_full = (repo / file).resolve()
        try:
            file_full.relative_to(_HOME)
        except ValueError:
            raise HTTPException(403, "File outside allowed root")

        git_args = ("diff", "--cached", "--", file) if staged \
                   else ("diff", "HEAD", "--", file)
        try:
            _, stdout, _ = await _git_run(*git_args, cwd=str(repo))
            diff = stdout
            # Untracked / brand-new file → show full content as all-added
            if not diff.strip() and file_full.is_file():
                try:
                    content = file_full.read_text(encoding="utf-8", errors="replace")
                    lines_out = [f"+{l}" for l in content.splitlines()]
                    diff = (f"--- /dev/null\n+++ b/{file}\n"
                            f"@@ -0,0 +1,{len(lines_out)} @@\n"
                            + "\n".join(lines_out))
                except Exception:
                    diff = ""
        except asyncio.TimeoutError:
            raise HTTPException(504, "git diff timed out")
        return {"diff": diff, "file": file}

    @router.post("/api/shell/gitstage")
    async def shell_gitstage(request: Request, req: _GitFileReq):
        """git add <file> (or all). Admin only."""
        _require_admin(request)
        repo = _safe_path(req.path)
        args = ("add", req.file) if req.file else ("add", "-A")
        rc, _, stderr = await _git_run(*args, cwd=str(repo))
        if rc != 0:
            raise HTTPException(400, stderr.strip())
        return {"ok": True}

    @router.post("/api/shell/gitunstage")
    async def shell_gitunstage(request: Request, req: _GitFileReq):
        """git restore --staged <file> (or all). Admin only."""
        _require_admin(request)
        repo = _safe_path(req.path)
        args = ("restore", "--staged", req.file) if req.file \
               else ("restore", "--staged", ".")
        rc, _, stderr = await _git_run(*args, cwd=str(repo))
        if rc != 0:
            raise HTTPException(400, stderr.strip())
        return {"ok": True}

    @router.post("/api/shell/gitrestore")
    async def shell_gitrestore(request: Request, req: _GitFileReq):
        """git restore <file> — discard working-tree changes. Admin only."""
        _require_admin(request)
        repo = _safe_path(req.path)
        args = ("restore", req.file) if req.file else ("restore", ".")
        rc, _, stderr = await _git_run(*args, cwd=str(repo))
        if rc != 0:
            raise HTTPException(400, stderr.strip())
        return {"ok": True}

    @router.get("/api/shell/gitinfo")
    async def shell_gitinfo(request: Request, path: str = ""):
        """Branch name, ahead/behind, change count, last commit. Admin only."""
        _require_admin(request)
        _reject_cross_site(request)
        target = _safe_path(path) if path else _HOME
        cwd = str(target)

        result = {"is_git": False, "branch": None, "ahead": 0, "behind": 0,
                  "changes": 0, "last_commit": None, "upstream": None}

        # Branch info + ahead/behind + change count — all from one command
        rc, stdout, _ = await _git_run(
            "status", "--branch", "--porcelain=2", "-uall", cwd=cwd
        )
        if rc == 128:          # not a git repo
            return result
        result["is_git"] = True
        change_count = 0
        for line in stdout.splitlines():
            if line.startswith("# branch.head "):
                result["branch"] = line[15:].strip()
            elif line.startswith("# branch.upstream "):
                result["upstream"] = line[19:].strip()
            elif line.startswith("# branch.ab "):
                parts = line[12:].split()
                if len(parts) == 2:
                    result["ahead"]  = int(parts[0].lstrip("+"))
                    result["behind"] = int(parts[1].lstrip("-"))
            elif not line.startswith("#"):
                change_count += 1
        result["changes"] = change_count

        # Last commit short hash + subject
        rc2, out2, _ = await _git_run(
            "log", "-1", "--format=%h %s", cwd=cwd
        )
        if rc2 == 0 and out2.strip():
            result["last_commit"] = out2.strip()

        return result

    @router.post("/api/shell/gitcommit")
    async def shell_gitcommit(request: Request, req: _GitCommitReq):
        """git commit -m <message>. Admin only."""
        _require_admin(request)
        msg = req.message.strip()
        if not msg:
            raise HTTPException(400, "Commit message required")
        repo = _safe_path(req.path)
        rc, stdout, stderr = await _git_run("commit", "-m", msg,
                                            cwd=str(repo), timeout=30)
        output = (stdout + stderr).strip()
        if rc != 0:
            raise HTTPException(400, output)
        return {"ok": True, "output": output}

    return router


# ── Interactive PTY — registered via app.mount() in app.py ───────────────────
# Using app.mount() with a pure ASGI callable bypasses FastAPI's routing and
# Starlette's BaseHTTPMiddleware middleware-stack snapshot, both of which can
# cause WebSocket routes added after middleware setup to return 404.

try:
    from winpty import PtyProcess as _WinPty
    _WINPTY_OK = True
except ImportError:
    _WinPty = None
    _WINPTY_OK = False

_PTY_HOME = Path(os.path.expanduser("~")).resolve()
_log = logging.getLogger(__name__)
_PTY_READ_BYTES = 65536


class _WinPtyAdapter:
    """Wrap pywinpty's PtyProcess in a uniform interface.
    Its read() takes a byte count and BLOCKS until data or EOF (raising
    EOFError at end) — so we run it in an executor thread."""
    def __init__(self, p): self._p = p
    def read_blocking(self):
        # pywinpty .read(num_bytes): blocks, returns str, raises EOFError at end
        return self._p.read(_PTY_READ_BYTES)
    def write(self, data): self._p.write(data)
    def set_size(self, rows, cols):
        try: self._p.setwinsize(rows, cols)
        except Exception: pass
    def alive(self):
        try: return self._p.isalive()
        except Exception: return False
    def kill(self):
        try: self._p.terminate(force=True)
        except Exception: pass


class _PosixPtyAdapter:
    """Wrap a POSIX master fd in the same interface. read_blocking() uses a
    blocking os.read on the master fd; EOF raises EOFError."""
    def __init__(self, master_fd, subproc):
        self._fd = master_fd
        self._sub = subproc
    def read_blocking(self):
        data = os.read(self._fd, _PTY_READ_BYTES)  # blocks; b'' = EOF
        if not data:
            raise EOFError
        return data.decode("utf-8", errors="replace")
    def write(self, data):
        os.write(self._fd, data.encode() if isinstance(data, str) else data)
    def set_size(self, rows, cols):
        try:
            import struct, termios as _t
            fcntl.ioctl(self._fd, _t.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception: pass
    def alive(self):
        return self._sub.returncode is None
    def kill(self):
        try: self._sub.terminate()
        except Exception: pass
        try: os.close(self._fd)
        except Exception: pass


def _pty_authorized(scope) -> bool:
    """Validate the session cookie directly from the ASGI scope. The PTY socket
    bypasses AuthMiddleware (it runs before the middleware stack), so we must
    enforce auth here — this endpoint is a full interactive shell."""
    app = scope.get("app")
    state = getattr(app, "state", None)
    # Auth explicitly disabled (AUTH_ENABLED=false) — dev mode, trust caller.
    if getattr(state, "auth_enabled", True) is False:
        return True
    # Loopback bypass (LOCALHOST_BYPASS=true) — trust requests from 127.0.0.1.
    if getattr(state, "localhost_bypass", False):
        client = scope.get("client") or ("", 0)
        if client[0] in ("127.0.0.1", "::1"):
            return True
    auth = getattr(state, "auth_manager", None)
    if auth is None:
        return True
    # Parse the Cookie header out of the raw ASGI headers
    cookie_hdr = ""
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            cookie_hdr = v.decode("latin-1")
            break
    token = None
    for part in cookie_hdr.split(";"):
        name, _, val = part.strip().partition("=")
        if name == "odysseus_session":
            token = val
            break
    if not token or not auth.validate_token(token):
        return False
    user = auth.get_username_for_token(token)
    return bool(user) and auth.is_admin(user)


async def _run_pty_session(ws, cwd: str, cols: int, rows: int) -> None:
    """Spawn a PTY shell and wire it bidirectionally to a WebSocket."""
    from starlette.websockets import WebSocketDisconnect as _WSD
    await ws.accept()

    try:
        cwd_path = Path(cwd).resolve()
        cwd_path.relative_to(_PTY_HOME)
        cwd_str = str(cwd_path)
    except Exception:
        cwd_str = str(_PTY_HOME)

    loop = asyncio.get_event_loop()
    adapter = None

    try:
        if IS_WINDOWS:
            if not _WINPTY_OK:
                await ws.send_text(
                    "\r\n\x1b[33mpywinpty not installed.\x1b[0m\r\n"
                    "Run:  pip install pywinpty  then restart the server.\r\n"
                )
                await ws.close()
                return
            shell = find_bash() or "cmd.exe"
            argv = [shell, "-l", "-i"] if "bash" in shell.lower() else [shell]
            p = _WinPty.spawn(argv, cwd=cwd_str, dimensions=(rows, cols))
            adapter = _WinPtyAdapter(p)
        else:
            if not PTY_SUPPORTED:
                await ws.send_text("\r\nPTY not supported on this platform.\r\n")
                await ws.close()
                return
            import struct, termios as _termios
            master_fd, slave_fd = pty.openpty()
            fcntl.ioctl(slave_fd, _termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            _bash = find_bash() or "/bin/bash"
            _subproc = await asyncio.create_subprocess_exec(
                _bash, "-l", "-i",
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                cwd=cwd_str, preexec_fn=os.setsid,
            )
            os.close(slave_fd)
            adapter = _PosixPtyAdapter(master_fd, _subproc)

        # Reader: blocking reads in a thread, forwarded to the socket.
        async def _reader():
            while True:
                try:
                    out = await loop.run_in_executor(None, adapter.read_blocking)
                except EOFError:
                    break
                except Exception:
                    break
                if out:
                    try:
                        await ws.send_text(out)
                    except Exception:
                        break
            try:
                await ws.send_text("\r\n\x1b[2m[session ended]\x1b[0m\r\n")
                await ws.close()
            except Exception:
                pass

        reader_task = asyncio.create_task(_reader())
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "input":
                    adapter.write(msg.get("data", ""))
                elif msg.get("type") == "resize":
                    adapter.set_size(int(msg.get("rows", rows)), int(msg.get("cols", cols)))
        except _WSD:
            pass
        except Exception:
            pass
        finally:
            reader_task.cancel()

    except Exception as exc:
        _log.error("PTY error: %s", exc, exc_info=True)
        try:
            await ws.send_text(f"\r\nPTY failed: {exc}\r\n")
            await ws.close()
        except Exception:
            pass
    finally:
        if adapter:
            adapter.kill()


async def pty_ws_asgi(scope, receive, send):
    """Pure ASGI handler for the interactive PTY WebSocket, dispatched by the
    middleware-stack interceptor in app.py (which runs before Starlette's
    snapshotted router, avoiding the WebSocket-route 404 problem)."""
    from starlette.websockets import WebSocket as _SW

    if scope["type"] != "websocket":
        await send({"type": "http.response.start", "status": 404,
                    "headers": [[b"content-length", b"0"]]})
        await send({"type": "http.response.body", "body": b""})
        return

    ws = _SW(scope, receive, send)

    if not _pty_authorized(scope):
        await ws.close(code=4403)  # policy violation: not an authenticated admin
        return

    from urllib.parse import parse_qs
    qs = parse_qs(scope.get("query_string", b"").decode())
    cwd = qs.get("cwd", [""])[0]
    cols = int(qs.get("cols", ["120"])[0])
    rows = int(qs.get("rows", ["30"])[0])
    await _run_pty_session(ws, cwd, cols, rows)
