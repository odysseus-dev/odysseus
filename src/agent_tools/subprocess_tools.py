import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import collections
import hashlib
from typing import Optional, Callable, Awaitable, Tuple, Dict
from core.platform_compat import IS_WINDOWS, find_bash, kill_process_tree
from src.constants import MAX_OUTPUT_CHARS

DEFAULT_BASH_TIMEOUT = 60 * 60     # 1 hour
DEFAULT_PYTHON_TIMEOUT = 60 * 60

PROGRESS_INTERVAL_S = 2.0
PROGRESS_TAIL_LINES = 12
TMUX_CAPTURE_LINES = 2000
_TMUX_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _posix_parent_map() -> dict[int, list[int]]:
    """Snapshot parent relationships using procfs, with a portable ps fallback."""
    children_by_parent: dict[int, list[int]] = collections.defaultdict(list)
    if sys.platform.startswith("linux"):
        try:
            for entry in os.scandir("/proc"):
                if not entry.name.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry.name}/stat", encoding="utf-8") as stat_file:
                        stat = stat_file.read()
                    fields = stat.rpartition(")")[2].split()
                    children_by_parent[int(fields[1])].append(int(entry.name))
                except (OSError, ValueError, IndexError):
                    continue
            return children_by_parent
        except OSError:
            children_by_parent.clear()

    try:
        output = subprocess.check_output(
            ["ps", "-eo", "pid=,ppid="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except Exception:
        return children_by_parent

    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, parent_pid = map(int, parts)
        except ValueError:
            continue
        children_by_parent[parent_pid].append(pid)
    return children_by_parent


def _linux_child_pids(parent_pid: int) -> list[int]:
    """Read direct children without scanning the system-wide process table."""
    try:
        with open(
            f"/proc/{parent_pid}/task/{parent_pid}/children", encoding="utf-8"
        ) as children_file:
            value = children_file.read()
    except OSError:
        return []
    children = []
    for item in value.split():
        try:
            children.append(int(item))
        except ValueError:
            pass
    return children


def _posix_descendant_pids(root_pid: int) -> list[int]:
    """Return a best-effort snapshot of descendants before their parent exits."""
    if sys.platform.startswith("linux"):
        descendants: list[int] = []
        seen: set[int] = set()
        pending = _linux_child_pids(root_pid)
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            descendants.append(pid)
            pending.extend(_linux_child_pids(pid))
        return descendants

    children_by_parent = _posix_parent_map()
    descendants = []
    seen: set[int] = set()
    pending = list(children_by_parent.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        pending.extend(children_by_parent.get(pid, ()))
    return descendants


def _posix_process_identity(pid: int) -> tuple[int, int] | None:
    """Return (process group, start identity) for safe delayed signalling."""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as stat_file:
                fields = stat_file.read().rpartition(")")[2].split()
            # After removing pid/comm, pgrp is field 5 and starttime is field 22.
            return int(fields[2]), int(fields[19])
        except (OSError, ValueError, IndexError):
            return None
    # Portable fallback cannot safely retain a PID beyond the current snapshot.
    return None


def _pid_is_confirmed_absent(pid: int) -> bool:
    """Return true only when the OS confirms no process owns this PID."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _kill_proc_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill an agent subprocess and descendants, including detached groups."""
    pid = getattr(proc, "pid", None)
    if not pid:
        return
    if IS_WINDOWS:
        kill_process_tree(pid)
        return

    descendants = _posix_descendant_pids(pid)
    own_group = os.getpgrp()
    root_group = getattr(proc, "_odysseus_pgid", None)
    root_identity = getattr(proc, "_odysseus_identity", None)
    # A Process object can outlive its numeric PID/PGID. Never signal a group
    # after Linux confirms that its leader PID has been reused by another
    # process; proc.kill() would be unsafe for the same reason.
    if root_identity is not None:
        live_root_identity = _posix_process_identity(root_group or pid)
        if live_root_identity is not None and live_root_identity != root_identity:
            return
    if root_group is None:
        try:
            root_group = os.getpgid(pid)
        except OSError:
            root_group = None

    detached_groups: set[int] = set()
    for child_pid in descendants:
        try:
            child_group = os.getpgid(child_pid)
        except OSError:
            continue
        if child_group not in {root_group, own_group}:
            detached_groups.add(child_group)

    try:
        if root_group is not None and root_group != own_group:
            os.killpg(root_group, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass

    for group in detached_groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    for child_pid in reversed(descendants):
        if child_pid == os.getpid():
            continue
        try:
            os.kill(child_pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def _kill_remembered_proc_group(
    proc: asyncio.subprocess.Process,
    descendants: dict[int, tuple[int, int]] | None = None,
) -> None:
    """Kill retained POSIX groups/PIDs after the process leader has exited."""
    if IS_WINDOWS:
        return
    own_group = os.getpgrp()
    root_group = getattr(proc, "_odysseus_pgid", None)
    verified: list[tuple[int, int]] = []
    for pid, identity in (descendants or {}).items():
        if _posix_process_identity(pid) == identity:
            verified.append((pid, identity[0]))
    groups = {group for _, group in verified if group != own_group}
    # A reused PGID requires a live process whose PID equals that PGID. The
    # original leader identity, or absence of such a PID, proves this is still
    # the retained command group; a mismatched identity means it was reused.
    root_identity = getattr(proc, "_odysseus_identity", None)
    live_root_identity = (
        _posix_process_identity(root_group) if root_group is not None else None
    )
    root_group_safe = root_group is not None and (
        (root_identity is not None and live_root_identity == root_identity)
        or (live_root_identity is None and _pid_is_confirmed_absent(root_group))
    )
    if root_group_safe or any(group == root_group for _, group in verified):
        groups.add(root_group)
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    for pid, _ in reversed(verified):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


async def _await_subprocess_creation(create_coro):
    """Make process creation cancellation-safe so a spawned child is never lost."""
    create_task = asyncio.create_task(create_coro)
    try:
        proc = await asyncio.shield(create_task)
        if not IS_WINDOWS:
            # Record the isolated group before returning to the caller. This is
            # also required on the cancellation path when the group leader has
            # already exited but one of its children is still alive.
            try:
                proc._odysseus_pgid = proc.pid
                proc._odysseus_identity = _posix_process_identity(proc.pid)
            except AttributeError:
                pass
        return proc
    except asyncio.CancelledError:
        try:
            proc = await asyncio.shield(create_task)
            if not IS_WINDOWS:
                try:
                    proc._odysseus_pgid = proc.pid
                    proc._odysseus_identity = _posix_process_identity(proc.pid)
                except AttributeError:
                    pass
            _kill_proc_tree(proc)
            await asyncio.shield(proc.wait())
        except Exception:
            pass
        raise


async def _create_bash_subprocess(command: str, **kwargs):
    """Start the agent shell with Bash semantics on every supported OS.

    ``asyncio.create_subprocess_shell`` delegates to ``cmd.exe`` on native
    Windows.  That contradicts the Bash tool contract and makes POSIX commands
    such as ``pwd``, ``ls -la``, and ``cat`` unreliable even when the launcher
    has found Git Bash.  Pass the selected workspace as a structural ``cwd``
    argument; Git Bash inherits that native Windows directory and exposes it
    using its normal ``/c/...`` representation.
    """
    if IS_WINDOWS:
        bash = find_bash()
        if not bash:
            raise RuntimeError(
                "Git Bash is required for the Bash tool on Windows; "
                "install Git for Windows and restart Odysseus"
            )
        return await _await_subprocess_creation(
            asyncio.create_subprocess_exec(bash, "-c", command, **kwargs)
        )
    kwargs.setdefault("start_new_session", True)
    proc = await _await_subprocess_creation(
        asyncio.create_subprocess_shell(command, **kwargs)
    )
    # Keep the isolated group ID after the leader exits. Background children
    # can retain stdout/stderr and outlive ``proc.wait()``.
    try:
        proc._odysseus_pgid = proc.pid
    except AttributeError:
        pass
    return proc


def _tmux_session_name(session_id: Optional[str]) -> str:
    raw = str(session_id or "default")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-") or "default"
    if safe == raw and len(safe) <= 80:
        return f"ody-agent-{safe}"

    # Sanitizing and truncating are lossy. Include the complete identifier in a
    # stable digest so unrelated chats cannot share a tmux session or its lock.
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    readable = safe[: 80 - len(digest) - 1].rstrip(".-") or "session"
    return f"ody-agent-{readable}-{digest}"


async def _run_exec(*args: str, timeout: float = 10) -> Tuple[str, str, int]:
    create_task = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        proc = await asyncio.shield(create_task)
    except asyncio.CancelledError:
        try:
            proc = await asyncio.shield(create_task)
            proc.kill()
            await asyncio.shield(proc.wait())
        except Exception:
            pass
        raise
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.shield(proc.wait())
        except Exception:
            pass
        raise
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            return "", "timeout", 124
        try:
            # Draining is required as well as waiting: a killed helper can remain
            # blocked in asyncio's subprocess transport when its pipes are full.
            await asyncio.wait_for(proc.communicate(), timeout=2)
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


async def _tmux_capture(name: str) -> str:
    out, _, _ = await _run_exec(
        "tmux", "capture-pane", "-p", "-J", "-S", f"-{TMUX_CAPTURE_LINES}", "-t", name,
        timeout=5,
    )
    return out


async def _tmux_send_line(name: str, line: str) -> None:
    if line:
        # Submit literal text and Enter in one tmux client request. Cancellation
        # cannot otherwise leave a partial command in a persistent pane.
        await _run_exec(
            "tmux", "send-keys", "-t", name, "-l", line,
            ";", "send-keys", "-t", name, "C-m", timeout=5,
        )
        return
    await _run_exec("tmux", "send-keys", "-t", name, "C-m", timeout=5)


async def _interrupt_tmux_command(
    name: str,
    protected_descendants: Optional[dict[int, tuple[int, int] | None]] = None,
) -> None:
    """Interrupt the active command without killing older persistent session jobs."""
    pane_pid = None
    out, _, rc = await _run_exec(
        "tmux", "display-message", "-p", "-t", name, "#{pane_pid}", timeout=3
    )
    if rc == 0:
        try:
            pane_pid = int(out.strip())
        except ValueError:
            pass

    descendants = _posix_descendant_pids(pane_pid) if pane_pid else []
    protected_identities = dict(protected_descendants or {})
    protected = {
        pid for pid, identity in protected_identities.items()
        if (identity is not None and _posix_process_identity(pid) == identity)
        or (identity is None and not _pid_is_confirmed_absent(pid))
    }
    # A persistent background job may fork after the command starts. Protect its
    # current subtree as well as the identity-verified initial snapshot.
    for pid in tuple(protected):
        for descendant in _posix_descendant_pids(pid):
            if not _pid_is_confirmed_absent(descendant):
                protected.add(descendant)
    descendants = [pid for pid in descendants if pid not in protected]
    await _run_exec("tmux", "send-keys", "-t", name, "C-c", timeout=3)
    if pane_pid is None:
        return

    # Revalidate prior jobs after C-c: an old PID may have exited/reused, while
    # a still-live old job may have forked. Expand only verified old roots before
    # reconciling the pane subtree so neither race causes escape or collateral kill.
    protected = {
        pid for pid, identity in protected_identities.items()
        if (identity is not None and _posix_process_identity(pid) == identity)
        or (identity is None and not _pid_is_confirmed_absent(pid))
    }
    for pid in tuple(protected):
        for descendant in _posix_descendant_pids(pid):
            if not _pid_is_confirmed_absent(descendant):
                protected.add(descendant)
    descendants = [
        pid for pid in _posix_descendant_pids(pane_pid) if pid not in protected
    ]

    try:
        pane_group = os.getpgid(pane_pid)
    except OSError:
        pane_group = None
    protected_groups: set[int] = set()
    for pid in protected:
        try:
            protected_groups.add(os.getpgid(pid))
        except OSError:
            pass
    groups: set[int] = set()
    for pid in descendants:
        try:
            group = os.getpgid(pid)
        except OSError:
            continue
        if group != pane_group and group not in protected_groups:
            groups.add(group)
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass
    for pid in reversed(descendants):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    ready_marker = f"__ODYSSEUS_INTERRUPT_READY_{time.time_ns()}__"
    await _tmux_send_line(name, f"printf '{ready_marker}\\n'")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if ready_marker in await _tmux_capture(name):
            return
        await asyncio.sleep(0.05)


async def _ensure_tmux_session(name: str, cwd: str, env: Optional[dict]) -> None:
    if await _tmux_has_session(name):
        await _run_exec("tmux", "send-keys", "-t", name, "stty -echo", "C-m", timeout=5)
        return
    await _run_exec(
        "tmux", "new-session", "-d", "-s", name, "-c", cwd,
        "env",
        f"TERM={env.get('TERM', 'xterm-256color') if env else 'xterm-256color'}",
        f"COLUMNS={env.get('COLUMNS', '120') if env else '120'}",
        f"LINES={env.get('LINES', '40') if env else '40'}",
        "/bin/bash",
        "--noprofile",
        "--norc",
        timeout=10,
    )
    if not await _tmux_has_session(name):
        raise RuntimeError(f"failed to create tmux session {name}")
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
    env: Optional[dict],
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    name = _tmux_session_name(session_id)
    lock = _TMUX_SESSION_LOCKS.setdefault(name, asyncio.Lock())
    async with lock:
        body = ""
        protected_descendants: dict[int, tuple[int, int] | None] = {}
        command_started = False
        # Conservative until the query completes: cancellation must never tear
        # down or interrupt a possibly pre-existing persistent session.
        session_existed = True
        try:
            session_existed = await _tmux_has_session(name)
            pane_out, _, pane_rc = await _run_exec(
                "tmux", "display-message", "-p", "-t", name, "#{pane_pid}", timeout=3
            )
            if pane_rc == 0:
                try:
                    for pid in _posix_descendant_pids(int(pane_out.strip())):
                        protected_descendants[pid] = _posix_process_identity(pid)
                except ValueError:
                    pass
            await _ensure_tmux_session(name, cwd, env)

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
                # The first completed line is a harmless start marker. Once it
                # has been submitted, later cancellation may need to interrupt
                # the command stream; cancellation before that preserves an
                # existing session because no user command can have run.
                command_started = True

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
                    return cleaned, "", rc, False
                if time.time() - started > timeout:
                    await _interrupt_tmux_command(name, protected_descendants)
                    cleaned = _clean_tmux_command_output(body, wrapped)
                    return cleaned, "", 124, True
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            if command_started:
                await asyncio.shield(
                    _interrupt_tmux_command(name, protected_descendants)
                )
            elif not session_existed:
                # Cancellation while creating a new persistent session must not
                # leave a half-configured tmux shell behind. Existing sessions
                # are intentionally preserved because no command was sent.
                await asyncio.shield(
                    _run_exec("tmux", "kill-session", "-t", name, timeout=3)
                )
            raise


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
        # ``Process.wait()`` may wait for inherited output pipes to close even
        # after the direct child exits. Poll returncode so an orphaned
        # background child cannot consume the entire command timeout.
        deadline = time.monotonic() + timeout
        observed_descendants: dict[int, tuple[int, int]] = {}
        while proc.returncode is None:
            if not IS_WINDOWS:
                for pid in _posix_descendant_pids(proc.pid):
                    identity = _posix_process_identity(pid)
                    if identity is not None:
                        observed_descendants[pid] = identity
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        # A successful shell may leave descendants behind even when they detach
        # into a new session and redirect inherited output. Retain snapshots while
        # the parent is alive so cleanup does not depend on an exited root PID.
        _kill_remembered_proc_group(proc, observed_descendants)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_proc_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
    except asyncio.CancelledError:
        _kill_proc_tree(proc)
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
        readers = asyncio.gather(rd_out, rd_err, return_exceptions=True)
        try:
            await asyncio.wait_for(asyncio.shield(readers), timeout=1)
        except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
            # The direct process exited, but a background child still owns an
            # inherited output pipe. Clean up its remembered process group.
            # Cancellation can arrive while draining these readers, after the
            # main cancellation handler is no longer active.
            _kill_proc_tree(proc)
            for t in (rd_out, rd_err):
                t.cancel()
            await readers
            if isinstance(exc, asyncio.CancelledError):
                raise

    return (
        "\n".join(stdout_full),
        "\n".join(stderr_full),
        proc.returncode,
        timed_out,
    )

class BashTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        if isinstance(content, dict):
            content = str(content.get("command") or content.get("cmd") or content.get("code") or "")
        progress_cb = ctx.get("progress_cb")
        _subproc_env = ctx.get("subproc_env")
        session_id = ctx.get("session_id")
        # tmux is a POSIX persistence path. A stray MSYS/Cygwin tmux.exe on
        # native Windows must not bypass the Git Bash launcher below: the tmux
        # setup hard-codes /bin/bash and cannot safely consume a native cwd.
        if session_id and not IS_WINDOWS and shutil.which("tmux"):
            stdout, stderr, rc, timed_out = await _run_tmux_bash(
                content,
                session_id=str(session_id),
                cwd=agent_cwd(),
                env=_subproc_env,
                timeout=DEFAULT_BASH_TIMEOUT,
                progress_cb=progress_cb,
            )
            if timed_out:
                return {
                    "error": f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — sent Ctrl-C to tmux session",
                    "exit_code": 124,
                    "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
                    "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
                    "tmux_session": _tmux_session_name(str(session_id)),
                }
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
            return {
                "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
                "exit_code": rc or 0,
                "tmux_session": _tmux_session_name(str(session_id)),
            }

        try:
            proc = await _create_bash_subprocess(
                content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
                cwd=agent_cwd(),
            )
        except RuntimeError as e:
            return {"error": f"bash: {e}", "exit_code": 1}
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc,
            timeout=DEFAULT_BASH_TIMEOUT,
            progress_cb=progress_cb,
        )
        if timed_out:
            return {"error": f"bash: timed out after {DEFAULT_BASH_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(stdout, MAX_OUTPUT_CHARS), "stderr": _truncate(stderr, MAX_OUTPUT_CHARS)}
        output = stdout.rstrip()
        err = stderr.rstrip()
        if err:
            output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
        output = _truncate(output, MAX_OUTPUT_CHARS)
        return {"output": output or "(no output)", "exit_code": rc or 0}

class PythonTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        progress_cb = ctx.get("progress_cb")
        _subproc_env = ctx.get("subproc_env")
        proc = await _await_subprocess_creation(
            asyncio.create_subprocess_exec(
                (sys.executable or "python"), "-I", "-c", content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
                cwd=agent_cwd(),
                **({"start_new_session": True} if not IS_WINDOWS else {}),
            )
        )
        if not IS_WINDOWS:
            try:
                proc._odysseus_pgid = proc.pid
                proc._odysseus_identity = _posix_process_identity(proc.pid)
            except AttributeError:
                pass
        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc,
            timeout=DEFAULT_PYTHON_TIMEOUT,
            progress_cb=progress_cb,
        )
        if timed_out:
            return {"error": f"python: timed out after {DEFAULT_PYTHON_TIMEOUT}s — process killed", "exit_code": 124, "stdout": _truncate(stdout, MAX_OUTPUT_CHARS), "stderr": _truncate(stderr, MAX_OUTPUT_CHARS)}
        output = stdout.rstrip()
        err = stderr.rstrip()
        if err:
            output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
        output = _truncate(output, MAX_OUTPUT_CHARS)
        return {"output": output or "(no output)", "exit_code": rc or 0}
