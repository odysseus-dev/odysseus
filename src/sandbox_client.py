"""Client for the Tier-B sandbox runner (docker/sandbox/runner.py).

When SANDBOX_RUNNER_SOCK points at the runner's Unix socket, the agent's
bash/python tool calls are dispatched into the isolated sandbox container
(network_mode:none, read-only rootfs, no secret/data/.ssh mounts) instead of
running as a local subprocess. See docs/security/bash-python-sandbox-scoping.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
from typing import Any, Dict, Optional


class SandboxUnavailable(Exception):
    """The sandbox runner is configured but could not be reached."""


def sandbox_socket_path() -> Optional[str]:
    """The runner socket path, or None when the sandbox is not configured."""
    return os.environ.get("SANDBOX_RUNNER_SOCK") or None


def sandbox_enabled() -> bool:
    return bool(sandbox_socket_path())


async def run_in_sandbox(
    tool: str,
    code: str,
    timeout: int = 120,
    sock_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute *code* (tool='bash'|'python') in the sandbox runner.

    Returns the runner's dict: {stdout, stderr, exit_code, timed_out, error}.
    Raises SandboxUnavailable if the runner can't be reached (socket missing,
    connection refused, or no response within the deadline) — the caller decides
    whether to fail closed or fall back to local execution.
    """
    sock = sock_path or sandbox_socket_path()
    if not sock:
        raise SandboxUnavailable("SANDBOX_RUNNER_SOCK is not set")

    payload = json.dumps({"tool": tool, "code": code, "timeout": timeout}).encode("utf-8")
    # Overall deadline = the task timeout plus slack for spawn + transport, so a
    # wedged runner can't hang the agent loop indefinitely.
    deadline = float(timeout) + 15.0

    async def _exchange() -> Dict[str, Any]:
        try:
            reader, writer = await asyncio.open_unix_connection(sock)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            raise SandboxUnavailable(f"cannot reach sandbox runner at {sock}: {e}")
        try:
            writer.write(struct.pack(">I", len(payload)) + payload)
            await writer.drain()
            hdr = await reader.readexactly(4)
            (n,) = struct.unpack(">I", hdr)
            body = await reader.readexactly(n)
        except (asyncio.IncompleteReadError, OSError) as e:
            raise SandboxUnavailable(f"sandbox runner transport error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        return json.loads(body.decode("utf-8"))

    try:
        return await asyncio.wait_for(_exchange(), timeout=deadline)
    except asyncio.TimeoutError:
        raise SandboxUnavailable(f"sandbox runner did not respond within {deadline:.0f}s")
