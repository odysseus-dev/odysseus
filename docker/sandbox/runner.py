#!/usr/bin/env python3
"""
runner.py — Tier-B sandbox exec server.

Listens on a Unix-domain socket and executes bash/python code sent by the
Odysseus app INSIDE this locked-down container: network_mode:none (no egress,
no internal services, no bridge), read-only rootfs, tmpfs /work, cap_drop ALL,
no-new-privileges, no secret/data/.ssh mounts. The container is the security
boundary; this process additionally scrubs the env and applies rlimits as
defense-in-depth. Self-contained — no Odysseus imports.

Why a Unix socket: with network_mode:none the runner has no network interfaces,
so app↔runner IPC rides a socket on a shared volume (filesystem, not network).

Wire protocol (framed JSON, 4-byte big-endian length prefix):
  request : {"tool":"bash"|"python","code":str,"timeout":int}
  response: {"stdout":str,"stderr":str,"exit_code":int,"timed_out":bool,"error":str|null}
"""
from __future__ import annotations

import json
import os
import resource
import signal
import socket
import struct
import subprocess
import sys
import threading

SOCK_PATH = os.environ.get("RUNNER_SOCK", "/ipc/runner.sock")
WORKDIR = os.environ.get("RUNNER_WORKDIR", "/work")
MAX_OUTPUT = int(os.environ.get("RUNNER_MAX_OUTPUT", str(256 * 1024)))
DEFAULT_TIMEOUT = int(os.environ.get("RUNNER_DEFAULT_TIMEOUT", "120"))
MAX_TIMEOUT = int(os.environ.get("RUNNER_MAX_TIMEOUT", "600"))

# Benign env allowlist — the child never sees secrets (the container has none
# mounted anyway; this is belt-and-suspenders).
_ENV_ALLOW = (
    "PATH", "TERM", "COLUMNS", "LINES", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
    "TZ", "TMPDIR", "PYTHONIOENCODING", "PYTHONUNBUFFERED",
)

_RLIMIT_CPU = int(os.environ.get("RUNNER_RLIMIT_CPU", "120"))
_RLIMIT_FSIZE = int(os.environ.get("RUNNER_RLIMIT_FSIZE", str(512 * 1024 * 1024)))
_RLIMIT_NPROC = int(os.environ.get("RUNNER_RLIMIT_NPROC", "512"))


def _child_env() -> dict:
    env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = WORKDIR
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "120"
    env["LINES"] = "40"
    return env


def _preexec() -> None:
    # New session → child is its own process-group leader, so a timeout can kill
    # the whole tree with killpg. Then lower resource limits (best-effort).
    os.setsid()

    def cap(which: int, soft: int) -> None:
        if soft <= 0:
            return
        try:
            hard = resource.getrlimit(which)[1]
            ceiling = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
            resource.setrlimit(which, (ceiling, hard))
        except (ValueError, OSError):
            pass

    cap(resource.RLIMIT_CPU, _RLIMIT_CPU)
    cap(resource.RLIMIT_FSIZE, _RLIMIT_FSIZE)
    if hasattr(resource, "RLIMIT_NPROC"):
        cap(resource.RLIMIT_NPROC, _RLIMIT_NPROC)


def _run(tool: str, code: str, timeout) -> dict:
    try:
        timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    if tool == "bash":
        argv = ["/bin/bash", "-c", code]
    elif tool == "python":
        argv = [sys.executable, "-I", "-c", code]
    else:
        return {"stdout": "", "stderr": "", "exit_code": 127, "timed_out": False,
                "error": f"unknown tool {tool!r}"}

    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=WORKDIR, env=_child_env(), preexec_fn=_preexec,
        )
    except Exception as e:  # noqa: BLE001
        return {"stdout": "", "stderr": "", "exit_code": 126, "timed_out": False,
                "error": f"spawn failed: {e}"}

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out, err = b"", b""

    def dec(b: bytes) -> str:
        s = (b or b"").decode("utf-8", "replace")
        return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n...[truncated]"

    rc = proc.returncode if proc.returncode is not None else -1
    return {"stdout": dec(out), "stderr": dec(err), "exit_code": rc,
            "timed_out": timed_out, "error": None}


# ── framed-JSON transport ────────────────────────────────────────────────

def _recvn(conn: socket.socket, n: int):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_frame(conn: socket.socket):
    hdr = _recvn(conn, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    if n > 8 * 1024 * 1024:  # 8 MB request cap
        return None
    return _recvn(conn, n)


def _send_frame(conn: socket.socket, data: bytes) -> None:
    conn.sendall(struct.pack(">I", len(data)) + data)


def _handle(conn: socket.socket) -> None:
    try:
        raw = _read_frame(conn)
        if raw is None:
            return
        req = json.loads(raw.decode("utf-8"))
        resp = _run(req.get("tool"), req.get("code", ""), req.get("timeout"))
    except Exception as e:  # noqa: BLE001
        resp = {"stdout": "", "stderr": "", "exit_code": 1, "timed_out": False,
                "error": f"runner error: {e}"}
    try:
        _send_frame(conn, json.dumps(resp).encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main() -> None:
    os.makedirs(WORKDIR, exist_ok=True)
    sock_dir = os.path.dirname(SOCK_PATH)
    if sock_dir:
        os.makedirs(sock_dir, exist_ok=True)
    if os.path.exists(SOCK_PATH):
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    # 0666 so the app container's non-root user (PUID) can connect; the volume is
    # shared only with the app, and the runner has no secrets to protect.
    try:
        os.chmod(SOCK_PATH, 0o666)
    except OSError:
        pass
    srv.listen(16)
    print(f"[sandbox-runner] listening on {SOCK_PATH} workdir={WORKDIR}", flush=True)

    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
