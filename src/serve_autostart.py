"""Relaunch Cookbook serve sessions that were running before a restart.

A container / app restart kills the tmux-hosted inference servers (vLLM,
llama.cpp, SGLang, Ollama) that Cookbook launched — the app itself never
resumed them, so a served model went dark until the user clicked **Serve**
again. This module reads the persisted Cookbook state and, for each LOCAL
serve task still marked ``running``, re-creates its tmux session using the
exact command that was originally launched, reusing the original ``sessionId``
so the existing UI task reconnects to the live session.

Design notes:
- Source of truth is ``data/cookbook_state.json`` → ``tasks[]``. A serve task
  carries the full launch command under ``payload._cmd``. When the user stops
  a serve the task is no longer ``running``, so stops are respected for free —
  there is no separate "should autostart" flag to keep in sync.
- Only LOCAL serves are replayed (``remoteHost`` empty). Remote serves live on
  another host whose lifecycle we don't manage here.
- The replay runner mirrors ``docker/entrypoint.sh``'s vLLM environment fixes
  (PATH to the pip ``--user`` bin, CUDA_HOME from the pip nvcc wheel, the
  FlashInfer-sampler workaround) so engine init doesn't crash looking for a
  system CUDA toolkit.
- Opt-in via the ``cookbook_serve_autostart`` setting (default on). Every step
  is defensive: this must never delay or crash app startup.
"""

import asyncio
import json
import logging
import os
import re
import shlex
from pathlib import Path

logger = logging.getLogger(__name__)

# Mirror cookbook_routes.TMUX_LOG_DIR / DATA_DIR so we read the same state and
# write runner scripts where the status poller already looks.
_STATE_PATH = Path(os.environ.get("DATA_DIR", "data")) / "cookbook_state.json"
_TMUX_DIR = Path("/tmp/odysseus-tmux")

# A serve task is only replayable if its command actually starts a server.
_SERVER_MARKERS = (
    "vllm serve",
    "llama-server",
    "llama_cpp",
    "sglang.launch_server",
    "ollama serve",
)


def _port_from_cmd(cmd: str) -> int | None:
    m = re.search(r"--port[= ](\d{2,5})", cmd)
    return int(m.group(1)) if m else None


def _port_open(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _runner_script(cmd: str, gpus: str) -> str:
    lines = [
        "#!/bin/bash",
        # pip install --user lands CLIs (vllm, etc.) under ~/.local/bin.
        'export PATH="$HOME/.local/bin:$PATH"',
        "export FLASHINFER_DISABLE_VERSION_CHECK=1",
        # FlashInfer JIT sampler needs nvcc + matching headers at startup;
        # disable it (sampler-only, no attention impact) — matches entrypoint.
        'export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"',
        # Point CUDA_HOME at the pip-wheel nvcc so vLLM engine init doesn't
        # crash on a missing /usr/local/cuda (mirrors docker/entrypoint.sh).
        'for cu in "$HOME"/.local/lib/python*/site-packages/nvidia/cu13 \\',
        '          "$HOME"/.local/lib/python*/site-packages/nvidia/cu12 \\',
        '          "$HOME"/.local/lib/python*/site-packages/nvidia/cuda_nvcc; do',
        '    [ -x "$cu/bin/nvcc" ] && export CUDA_HOME="$cu" && break',
        "done",
    ]
    if gpus.strip():
        lines.append(f"export CUDA_VISIBLE_DEVICES={shlex.quote(gpus.strip())}")
    lines.append(cmd)
    lines.append("echo")
    lines.append('echo "=== Process exited with code $? ==="')
    # Keep the pane alive after exit so the status poller can read errors,
    # matching the interactive serve flow in cookbook_routes.
    lines.append("exec bash -i")
    return "\n".join(lines) + "\n"


async def _tmux_has_session(sid: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "has-session", "-t", sid,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        # No tmux on PATH — nothing we can do; caller logs the launch failure.
        return False


async def _launch(sid: str, cmd: str, gpus: str) -> None:
    _TMUX_DIR.mkdir(parents=True, exist_ok=True)
    runner = _TMUX_DIR / f"{sid}_run.sh"
    runner.write_text(_runner_script(cmd, gpus), encoding="utf-8")
    os.chmod(runner, 0o755)
    proc = await asyncio.create_subprocess_exec(
        "tmux", "new-session", "-d", "-s", sid, str(runner),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError((err.decode(errors="replace").strip() or "tmux launch failed"))


def _collect_candidates(state: dict) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for t in state.get("tasks") or []:
        if t.get("type") != "serve" or t.get("status") != "running":
            continue
        if (t.get("remoteHost") or "").strip():
            continue  # local serves only
        payload = t.get("payload") or {}
        cmd = (payload.get("_cmd") or "").strip()
        sid = t.get("sessionId") or t.get("id")
        if not cmd or not sid:
            continue
        if not any(m in cmd for m in _SERVER_MARKERS):
            continue
        out.append((sid, cmd, str(payload.get("_gpus") or "")))
    return out


async def autostart_serves(delay: float = 5.0) -> None:
    """Replay running local Cookbook serves after a restart. Never raises."""
    try:
        from src.settings import get_setting
        if not get_setting("cookbook_serve_autostart", True):
            return
        if not _STATE_PATH.exists():
            return
        state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        candidates = _collect_candidates(state)
        if not candidates:
            return
        # Let the box settle (GPU visible, filesystem mounts ready) before we
        # spin up a multi-GB model load.
        await asyncio.sleep(delay)
        for sid, cmd, gpus in candidates:
            try:
                port = _port_from_cmd(cmd)
                if port and await asyncio.to_thread(_port_open, port):
                    logger.info("[serve-autostart] %s: port %s already serving, skip", sid, port)
                    continue
                if await _tmux_has_session(sid):
                    logger.info("[serve-autostart] %s: tmux session already exists, skip", sid)
                    continue
                await _launch(sid, cmd, gpus)
                logger.info("[serve-autostart] relaunched %s (port %s)", sid, port)
            except Exception as e:
                logger.warning("[serve-autostart] failed to relaunch %s: %s", sid, e)
    except Exception as e:
        logger.warning("[serve-autostart] skipped: %s", e)
