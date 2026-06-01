"""
builtin_mcp.py

Auto-registration of built-in MCP servers on startup.
Each server runs as a stdio subprocess managed by McpManager.
"""

import logging
import os
import shutil
import sys
import asyncio
from typing import Set

logger = logging.getLogger(__name__)

# In-flight NPX connect tasks. wait_for in _start_npx_servers shields these
# from cancellation (cancelling mid-handshake triggers a cancel-scope crash
# inside mcp.client.stdio's anyio task group — see _start_npx_servers and
# _drain_pending_npx_tasks for full context). We track them so we can
# consume their exceptions and drain them at shutdown rather than leaking
# tasks and subprocesses.
_pending_npx_tasks: Set[asyncio.Task] = set()


def _npx_task_done(task: asyncio.Task) -> None:
    """done_callback for shielded NPX connect tasks. Removes from the
    pending set and consumes any exception so we don't get a noisy
    'Task exception was never retrieved' at shutdown."""
    _pending_npx_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            f"NPX MCP connect task '{task.get_name()}' finished with error: "
            f"{type(exc).__name__}: {exc}"
        )


async def drain_pending_npx_tasks(timeout_s: float = 5.0) -> None:
    """Drain any still-pending NPX connect tasks at app shutdown.

    Called from app.py's on_event('shutdown') handler. Gives shielded
    NPX connect tasks a brief chance to finish on their own (the
    subprocess may exit, the handshake may complete) before the event
    loop closes. Tasks that don't finish are left to be cleaned up by
    asyncio's shutdown_asyncgens — they may emit one cosmetic
    'Attempted to exit cancel scope' ERROR line per remaining task as
    mcp.client.stdio's async generator is force-closed, but that's a
    library-level limitation and doesn't affect exit code or behavior.
    """
    pending = list(_pending_npx_tasks)
    if not pending:
        return
    logger.info(f"Draining {len(pending)} pending NPX MCP connect task(s)...")
    done, still_pending = await asyncio.wait(pending, timeout=timeout_s)
    if still_pending:
        logger.warning(
            f"{len(still_pending)} NPX MCP task(s) didn't finish in {timeout_s}s "
            f"at shutdown; their async generators will be force-closed."
        )


def _find_npx() -> str:
    """Find npx binary, checking common locations if not on PATH."""
    npx = shutil.which("npx")
    if npx:
        return npx
    # Common locations when PATH is minimal (e.g. systemd)
    for candidate in [
        os.path.expanduser("~/.npm-global/bin/npx"),
        os.path.expanduser("~/.local/bin/npx"),
        "/usr/local/bin/npx",
        "/usr/bin/npx",
    ]:
        if os.path.isfile(candidate):
            return candidate
    # Try to find node and use npx from same dir
    node = shutil.which("node")
    if node:
        npx_candidate = os.path.join(os.path.dirname(node), "npx")
        if os.path.isfile(npx_candidate):
            return npx_candidate
    return "npx"  # fallback, will fail with a clear error

# Server definitions: id -> (script path relative to project root, display name)
#
# bash / python / filesystem / web_search were folded into native in-process
# execution (src/tool_execution.py:_direct_fallback). Those trivial subprocess
# wrappers are gone.
#
# image_gen / memory / rag / email still run as stdio MCP servers — each
# carries hundreds of LOC of unique IMAP / HTTP / manager logic not worth
# duplicating into the native path right now.
_BUILTIN_SERVERS = {
    "image_gen":  ("mcp_servers/image_gen_server.py",  "Built-in: Image Generation"),
    "memory":     ("mcp_servers/memory_server.py",     "Built-in: Memory"),
    "rag":        ("mcp_servers/rag_server.py",        "Built-in: RAG"),
    "email":      ("mcp_servers/email_server.py",      "Built-in: Email"),
}

# NPX-based built-in servers (run via npx, not Python)
_BUILTIN_NPX_SERVERS = {
    "builtin_browser": {
        "name": "Built-in: Browser",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--headless", "--caps", "vision"],
    },
}

# Global flag to disable MCP if there are compatibility issues
MCP_DISABLED = os.environ.get("ODYSSEUS_DISABLE_MCP", "").lower() in ("1", "true", "yes")


async def register_builtin_servers(mcp_manager):
    """Connect all built-in MCP servers to the manager."""
    if MCP_DISABLED:
        logger.info("Built-in MCP servers disabled via ODYSSEUS_DISABLE_MCP")
        return

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python = sys.executable

    async def _connect_python_server(server_id: str, script_path: str, name: str):
        try:
            ok = await mcp_manager.connect_server(
                server_id=server_id,
                name=name,
                transport="stdio",
                command=python,
                args=[script_path],
                env={"PYTHONPATH": base_dir},
            )
            if ok:
                logger.info(f"Built-in MCP server registered: {name}")
            else:
                logger.warning(f"Built-in MCP server failed to connect: {name}")
        except asyncio.CancelledError:
            logger.warning(f"Built-in MCP server {name} cancelled")
            raise
        except BaseException as e:
            logger.warning(f"Built-in MCP server {name} error: {type(e).__name__}: {e}")

    for server_id, (script, name) in _BUILTIN_SERVERS.items():
        script_path = os.path.join(base_dir, script)
        if not os.path.exists(script_path):
            logger.warning(f"Built-in MCP server script not found: {script_path}")
            continue
        asyncio.create_task(_connect_python_server(server_id, script_path, name))

    # Register NPX-based servers in the background (they take longer to start)
    npx_path = _find_npx()
    logger.info(f"NPX binary resolved to: {npx_path}")

    async def _start_npx_servers():
        await asyncio.sleep(3)  # let Python servers finish first
        for server_id, cfg in _BUILTIN_NPX_SERVERS.items():
            logger.info(f"Starting NPX server: {cfg['name']} ({npx_path} {' '.join(cfg['args'])})")

            # mcp.client.stdio.stdio_client opens an anyio task group
            # internally. If wait_for cancels the connect mid-handshake,
            # that task group can't be exited from a different task and
            # anyio raises "Attempted to exit cancel scope in a different
            # task than it was entered in" in a sibling task. The error
            # isn't caught by the surrounding except BaseException (wrong
            # task), and the orphaned cancel scope cascades cancellations
            # into the rest of the event loop and downs the app.
            #
            # Fix: wrap the connect task in asyncio.shield so wait_for's
            # cancellation can't reach it. Track the task in
            # _pending_npx_tasks with a done_callback that consumes its
            # eventual exception, and drain still-pending tasks at
            # shutdown (see drain_pending_npx_tasks, hooked from app.py's
            # on_event('shutdown')).
            connect_task = asyncio.create_task(
                mcp_manager.connect_server(
                    server_id=server_id,
                    name=cfg["name"],
                    transport="stdio",
                    command=npx_path,
                    args=cfg["args"],
                ),
                name=f"mcp-npx-connect-{server_id}",
            )
            _pending_npx_tasks.add(connect_task)
            connect_task.add_done_callback(_npx_task_done)

            try:
                ok = await asyncio.wait_for(asyncio.shield(connect_task), timeout=30)
                if ok:
                    logger.info(f"Built-in NPX server registered: {cfg['name']}")
                else:
                    logger.warning(f"Built-in NPX server failed to connect: {cfg['name']}")
            except asyncio.TimeoutError:
                logger.warning(
                    f"Built-in NPX server '{cfg['name']}' didn't connect within 30s; "
                    f"continuing without it (task drained at shutdown)"
                )
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                logger.warning(f"Built-in NPX server {cfg['name']} error: {type(e).__name__}: {e}")

    asyncio.create_task(_start_npx_servers())
