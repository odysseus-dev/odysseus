import asyncio
import codecs
import os
import subprocess
import sys
import time
import collections
from typing import Optional, Callable, Awaitable, Tuple, Dict
from core.platform_compat import IS_WINDOWS, find_bash, kill_process_tree
from src.constants import MAX_OUTPUT_CHARS

DEFAULT_BASH_TIMEOUT = 60 * 60     # 1 hour
DEFAULT_PYTHON_TIMEOUT = 60 * 60

PROGRESS_INTERVAL_S = 2.0
PROGRESS_TAIL_LINES = 12


async def _create_bash_subprocess(command: str, **kwargs):
    """Start a fresh, non-interactive Bash process on every platform.

    Never delegate to ``/bin/sh`` and never make behavior depend on whether
    tmux happens to be installed. A fresh process also makes the selected
    workspace and environment deterministic for every tool call.
    """
    bash = find_bash()
    if not bash:
        hint = "install Git for Windows" if IS_WINDOWS else "install bash"
        raise RuntimeError(f"Bash was not found; {hint} and restart Odysseus")
    if IS_WINDOWS:
        kwargs.setdefault(
            "creationflags", getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs.setdefault("start_new_session", True)
    return await asyncio.create_subprocess_exec(
        bash, "--noprofile", "--norc", "-c", command, **kwargs
    )


class _BoundedOutput:
    """Keep the beginning and end of a command log within a fixed budget."""

    def __init__(self, limit: int = MAX_OUTPUT_CHARS):
        self.limit = max(256, int(limit))
        self.head_limit = self.limit // 2
        self.tail_limit = self.limit - self.head_limit
        self.head = ""
        self.tail = ""
        self.total = 0

    def append(self, text: str) -> None:
        if not text:
            return
        self.total += len(text)
        remaining = text
        if len(self.head) < self.head_limit:
            take = min(self.head_limit - len(self.head), len(remaining))
            self.head += remaining[:take]
            remaining = remaining[take:]
        if remaining:
            self.tail = (self.tail + remaining)[-self.tail_limit:]

    def text(self) -> str:
        if self.total <= self.limit:
            return self.head + self.tail
        omitted = self.total - len(self.head) - len(self.tail)
        return self.head + f"\n... ({omitted} chars omitted) ...\n" + self.tail


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Stop the command and its children, then reap the direct process."""
    pid = getattr(proc, "pid", None)
    if pid:
        await asyncio.to_thread(kill_process_tree, pid)
    else:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except Exception:
        if pid:
            await asyncio.to_thread(kill_process_tree, pid, True)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass

async def _run_subprocess_streaming(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    started = time.time()
    stdout_full = _BoundedOutput()
    stderr_full = _BoundedOutput()
    tail = collections.deque(maxlen=PROGRESS_TAIL_LINES)

    async def _reader(stream, full_buf, label: str):
        if stream is None:
            return
        pending = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            decoded = decoder.decode(chunk)
            full_buf.append(decoded)
            pending += decoded
            lines = pending.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                pending = lines.pop()
            else:
                pending = ""
            for line in lines:
                clean = line.rstrip("\r\n")
                tail.append(f"! {clean}" if label == "err" else clean)
        final = decoder.decode(b"", final=True)
        if final:
            full_buf.append(final)
            pending += final
        if pending:
            tail.append(f"! {pending}" if label == "err" else pending)

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
        await _terminate_process_tree(proc)
    except asyncio.CancelledError:
        await _terminate_process_tree(proc)
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
        stdout_full.text().rstrip("\n"),
        stderr_full.text().rstrip("\n"),
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
        return {
            "output": output or "(no output)",
            "stdout": _truncate(stdout, MAX_OUTPUT_CHARS),
            "stderr": _truncate(stderr, MAX_OUTPUT_CHARS),
            "exit_code": rc if rc is not None else 1,
        }

class PythonTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        progress_cb = ctx.get("progress_cb")
        _subproc_env = ctx.get("subproc_env")
        proc = await asyncio.create_subprocess_exec(
            (sys.executable or "python"), "-I", "-c", content,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_subproc_env,
            cwd=agent_cwd(),
        )
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
