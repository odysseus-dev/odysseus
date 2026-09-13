"""
mcp_manager.py

Manages connections to MCP (Model Context Protocol) tool servers.
Each server exposes tools that are made available to the agent loop.
"""

import json
import logging
import os
import re
import asyncio 
from typing import Any, Dict, List, Optional, Set, Tuple
from src.database import McpServer, SessionLocal

from src.runtime_paths import get_app_root

logger = logging.getLogger(__name__)

# Lotus is filtered per request in agent_loop according to the owner's
# local/LAN/API access policy. Approved endpoints may use it; denied endpoints
# have every Lotus tool hidden and runtime-blocked.
_BUILTIN_FUNCTION_CALLING_SERVERS = {
    "builtin_browser",
    "todoist",
    "lotus",
    "pi_worker",
    "github_read",
    "github_write",
}


def _model_visible_schema(schema: Any) -> Dict:
    """Remove dispatcher-injected arguments from a model-facing MCP schema."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    visible = dict(schema)
    properties = dict(visible.get("properties") or {})
    hidden = {name for name in properties if name.startswith("_odysseus_")}
    for name in hidden:
        properties.pop(name, None)
    visible["properties"] = properties
    if isinstance(visible.get("required"), list):
        visible["required"] = [
            name for name in visible["required"] if name not in hidden
        ]
    return visible

def _describe_exception(error: Optional[BaseException]) -> str:
    """Render an exception for logs/error payloads, never as an empty string.

    Several MCP-adjacent exceptions (anyio's ClosedResourceError/
    BrokenResourceError when a stdio subprocess's pipe closes underneath it,
    among others) carry no message -- str(e) is "". `str(error) if error
    else "Unknown error"` only catches error being None/falsy, not an
    Exception instance whose str() is empty (Exception instances are always
    truthy), so that fallback silently produced e.g. "MCP tool call failed:
    mcp__xyz__tool: " with nothing after the colon -- undiagnosable from
    logs alone. Fall back to the exception's type name so there's always
    something to search for/report.
    """
    if error is None:
        return "Unknown error"
    text = str(error).strip()
    return text if text else f"{type(error).__name__} (no error message — check the MCP server's own logs/stderr)"


# Exceptions that mean "the pipe to the MCP server is gone", as opposed to "the
# tool ran and failed". A stdio server whose subprocess has exited fails the very
# next call in ~1ms, before any bytes are written, so such a call provably had no
# side effects and is safe to replay on a fresh connection.
_DEAD_TRANSPORT_ERRORS = {
    "ClosedResourceError",   # anyio: stream closed underneath us
    "BrokenResourceError",   # anyio: peer went away mid-write
    "EndOfStream",           # anyio: reader hit EOF (subprocess exited)
    "BrokenPipeError",
    "ConnectionResetError",
    "ProcessLookupError",
}


def _is_dead_transport_error(error: Optional[BaseException]) -> bool:
    """True when an exception means the transport died, not that the tool failed."""
    if error is None:
        return False
    if isinstance(error, (BrokenPipeError, ConnectionResetError, ProcessLookupError)):
        return True
    return type(error).__name__ in _DEAD_TRANSPORT_ERRORS


# How much of a dead server's stderr to quote back to the agent/logs.
_STDERR_TAIL_BYTES = 2000


def _format_mcp_connection_error(name: str, command: str = "", args: Optional[List[str]] = None, error: Exception = None) -> str:
    """Return a user-actionable MCP connection error message."""
    args = args or []
    raw_error = _describe_exception(error)
    command_line = " ".join([command or "", *args]).strip()
    lower_command = command_line.lower()

    if "@playwright/mcp" in lower_command:
        return (
            f"{raw_error}\n\n"
            "Browser MCP could not start. On fresh installs, cache the Playwright MCP package once before connecting:\n\n"
            "npx -y @playwright/mcp@latest --version\n\n"
            "Then restart Odysseus and reconnect the Browser MCP server."
        )

    if name.lower() == "todoist" or lower_command.startswith("td "):
        return (
            f"{raw_error}\n\n"
            "Todoist MCP could not start. The `td` binary is the Todoist CLI, not an MCP server, "
            "so adding `td` directly as a custom MCP server will close the connection during handshake.\n\n"
            "Use the built-in Todoist MCP server, or run the wrapper as a stdio server:\n\n"
            "python /app/mcp_servers/todoist_server.py"
        )

    return raw_error


# Caps for rendering untrusted MCP tool schemas into the agent prompt (issue #2660).
# MCP servers are third-party/user-added, so field names and parameter counts are
# untrusted input — bound them so an odd or hostile schema cannot distort the prompt.
_MCP_PARAM_MAX = 12   # max params rendered per tool
_MCP_TOKEN_MAX = 40   # max chars per rendered name / type token
_MCP_HINT_MAX = 300   # total-length backstop for the whole hint


def _sanitize_schema_token(value: Any, limit: int = _MCP_TOKEN_MAX) -> str:
    """Make an untrusted JSON-Schema token safe to splice into the prompt.

    Replaces control chars / newlines with a space, collapses whitespace, and
    length-caps the result, so a weird field name or type cannot inject newlines
    or run on. Normal short identifiers pass through unchanged.
    """
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _format_mcp_params(input_schema: Any) -> str:
    """Render an MCP tool's JSON-Schema inputs as a compact prompt hint.

    Without this the agent only sees a tool's name + description and has to
    guess its arguments (issue #2509). Produces e.g.
    ` Args (JSON): {"path": string (required), "limit": integer}` — names,
    coarse types, and required-ness, kept short so it stays prompt-friendly.
    Returns "" when there are no parameters.

    MCP servers are third-party, so names/types are sanitized and the parameter
    count + total length are capped (issue #2660); normal schemas are unaffected.
    """
    if not isinstance(input_schema, dict):
        return ""
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(input_schema.get("required") or [])
    parts = []
    for pname, pinfo in list(props.items())[:_MCP_PARAM_MAX]:
        pinfo = pinfo if isinstance(pinfo, dict) else {}
        ptype = pinfo.get("type") or "any"
        if isinstance(ptype, list):
            ptype = "|".join(str(x) for x in ptype)
        tag = f'"{_sanitize_schema_token(pname)}": {_sanitize_schema_token(ptype)}'
        if pname in required:
            tag += " (required)"
        parts.append(tag)
    extra = len(props) - len(parts)
    if extra > 0:
        parts.append(f"…+{extra} more")
    hint = " Args (JSON): {" + ", ".join(parts) + "}"
    if len(hint) > _MCP_HINT_MAX:
        hint = hint[:_MCP_HINT_MAX - 1].rstrip() + "…"
    return hint


# Tool-name prefixes that denote a read-only/inspection operation. Used to
# classify MCP tools for plan mode when the server provides no readOnlyHint.
# These are PREFIXES, not whole words (matched via str.startswith below), so a
# stem like "summar" intentionally covers "summarise"/"summarize"/"summary".
_MCP_READONLY_VERBS = (
    "list", "get", "read", "search", "fetch", "query", "find", "describe",
    "show", "view", "lookup", "count", "status", "info", "inspect", "summar",
)


def mcp_tool_is_readonly(tool: Dict) -> bool:
    """Classify an MCP tool as safe (non-mutating) for plan mode.

    Prefer the server's own annotations (readOnlyHint / destructiveHint). When
    absent, fall back to a tool-name verb heuristic, and FAIL CLOSED (treat as
    write) for anything that doesn't clearly read — plan mode must not run a
    write tool just because its intent is ambiguous.
    """
    ann = tool.get("annotations")
    # annotations may be a dict or a pydantic model
    read_hint = None
    destructive = None
    if ann is not None:
        if isinstance(ann, dict):
            read_hint = ann.get("readOnlyHint")
            destructive = ann.get("destructiveHint")
        else:
            read_hint = getattr(ann, "readOnlyHint", None)
            destructive = getattr(ann, "destructiveHint", None)
    if read_hint is True:
        return True
    if read_hint is False or destructive is True:
        return False
    # No usable hint — heuristic on the tool name's leading verb.
    name = (tool.get("name") or "").lower()
    return name.startswith(_MCP_READONLY_VERBS)


class _ServerLock:
    """A per-server mutex that a single task may re-enter.

    Every mutation of McpManager's state dicts for one server_id runs under
    this lock so overlapping connect/disconnect cycles serialize instead of
    interleaving (see McpManager.restart_server for the bug this fixes).

    It has to be re-entrant *within one task* because the public entry points
    nest: _reconnect_server() takes the lock and then calls the public
    disconnect_server()/connect_server(), which take it again. A plain
    asyncio.Lock would self-deadlock there. Re-entry is keyed on the owning
    asyncio.Task, so a *different* task still blocks — which is exactly the
    serialization we need between concurrent HTTP requests.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None
        self._depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._depth -= 1
        if self._depth <= 0:
            self._owner = None
            self._depth = 0
            self._lock.release()
        return False


class McpManager:
    """Manages MCP server connections and tool routing."""

    def __init__(self):
        # server_id -> connection state
        self._connections: Dict[str, Dict[str, Any]] = {}
        # server_id -> list of tool schemas
        self._tools: Dict[str, List[Dict]] = {}
        # server_id -> MCP ClientSession
        self._sessions: Dict[str, Any] = {}
        # server_id -> exit stack (for cleanup)
        self._stacks: Dict[str, Any] = {}
        # server_id -> background connect task (HTTP transport / OAuth)
        self._connect_tasks: Dict[str, Any] = {}
        # server_id -> connect_server() kwargs, so a crashed server can be
        # restarted on demand without a DB round-trip (see _reconnect_server).
        self._configs: Dict[str, Dict[str, Any]] = {}
        # server_id -> open stderr capture file {"path": str, "handle": file}
        self._stderr_logs: Dict[str, Dict[str, Any]] = {}
        # server_id -> (event loop, _ServerLock) serializing every mutation of
        # the dicts above for that server. Created lazily so McpManager can be
        # constructed at import time, before any loop exists.
        self._locks: Dict[str, Tuple[Any, Any]] = {}
        # server_id -> in-flight restart_server() task, so concurrent reconnect
        # requests for one server join the running restart instead of stacking
        # up another disconnect/connect cycle each.
        self._restart_tasks: Dict[str, Any] = {}
        # Tracking updates to tools/connections for RAG indexing / prompt cache
        self._generation = 0

    def _get_lock(self, server_id: str) -> "_ServerLock":
        """Return this server's mutex, creating it on first use.

        Must be called from inside a running loop. The loop is remembered
        because asyncio primitives bind to the loop that first awaits them:
        tests (and anything else) that drive the same manager through several
        asyncio.run() calls would otherwise hit "bound to a different event
        loop". A loop change means the previous loop is gone, so the old lock
        cannot still be held and is safe to replace.
        """
        loop = asyncio.get_running_loop()
        entry = self._locks.get(server_id)
        if entry is None or entry[0] is not loop:
            entry = (loop, _ServerLock())
            self._locks[server_id] = entry
        return entry[1]

    async def connect_server(
        self,
        server_id: str,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
    ) -> bool:
        """Connect to an MCP server via stdio, SSE, or Streamable HTTP transport."""
        async with self._get_lock(server_id):
            return await self._connect_server_unlocked(
                server_id=server_id,
                name=name,
                transport=transport,
                command=command,
                args=args,
                env=env,
                url=url,
            )

    async def _connect_server_unlocked(
        self,
        server_id: str,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
    ) -> bool:
        """connect_server() body. Callers must hold this server's lock."""
        # Never let a second connect orphan a live transport: overwriting
        # self._stacks[server_id] used to leave the previous stdio subprocess
        # running with nothing referencing its AsyncExitStack, so it was torn
        # down later by the garbage collector from an unrelated task -- the
        # "Attempted to exit cancel scope in a different task" RuntimeError
        # that showed up as an unretrieved task exception in the logs.
        if server_id in self._stacks or server_id in self._sessions:
            await self._disconnect_server_unlocked(server_id, log=False)

        self._configs[server_id] = {
            "server_id": server_id,
            "name": name,
            "transport": transport,
            "command": command,
            "args": list(args or []),
            "env": dict(env or {}),
            "url": url,
        }
        try:
            if transport == "stdio":
                res = await self._connect_stdio(server_id, name, command, args or [], env or {})
            elif transport == "sse":
                res = await self._connect_sse(server_id, name, url)
            elif transport == "http":
                res = await self._start_http_connect(server_id, name, url)
            else:
                logger.error(f"Unknown MCP transport: {transport}")
                res = False
            if res:
                self._generation += 1
            return res
        except Exception as e:
            logger.error(f"Failed to connect MCP server {name} ({server_id}): {_describe_exception(e)}")
            error_message = _format_mcp_connection_error(name, command or "", args or [], e)
            # A server that dies during the handshake usually explains itself on
            # stderr while the client-side exception says nothing useful.
            tail = self._read_stderr_tail(server_id)
            if tail:
                error_message = f"{error_message}\n\nLast output from the server:\n{tail}"
            self._connections[server_id] = {"status": "error", "error": error_message, "name": name}
            self._generation += 1
            return False

    async def _connect_stdio(self, server_id: str, name: str, command: str, args: List[str], env: Dict[str, str]) -> bool:
        """Connect to an MCP server via stdio transport.

        Known limitation: stdio_client() opens an anyio task group, and anyio
        cancel scopes may only be exited by the task that entered them. The
        AsyncExitStack built here therefore belongs to whichever task ran the
        connect (a FastAPI request task, or restart_server's own task), while
        disconnect_server() closes it from whatever task asks for the
        disconnect later -- so `await stack.aclose()` can raise "Attempted to
        exit cancel scope in a different task than it was entered in". That
        warning is logged and swallowed; the subprocess still dies with its
        pipes. Removing it entirely means owning each stdio server's stack in
        one long-lived supervisor task per server and driving connect/
        disconnect through a queue, which is a much larger change. Serializing
        the callers (see restart_server) removes the state corruption and the
        orphaned-subprocess storm that made the warning fire repeatedly.
        """
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from contextlib import AsyncExitStack

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**os.environ, **env} if env else None,
            )

            stack = AsyncExitStack()
            registered = False

            try:
                # Capture the subprocess's stderr to a file instead of letting it
                # vanish into the app's own stderr. When the process dies, the
                # client-side exception is often anyio's ClosedResourceError with
                # no message at all; this tail is the only thing that says *why*.
                errlog = self._open_stderr_log(server_id)
                if errlog is not None:
                    transport = await stack.enter_async_context(stdio_client(server_params, errlog=errlog))
                else:
                    transport = await stack.enter_async_context(stdio_client(server_params))
                read_stream, write_stream = transport
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

                await session.initialize()
                tools_result = await session.list_tools()

                tools = []
                for tool in tools_result.tools:
                    tools.append({
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                        # MCP tool annotations (readOnlyHint / destructiveHint) drive
                        # plan-mode read-only gating. Absent on many servers, so we
                        # fall back to a name heuristic in mcp_tool_is_readonly().
                        "annotations": getattr(tool, "annotations", None),
                    })

                # Extract identity hints from env vars (e.g. email address, API name)
                # so tool descriptions can distinguish between multiple instances of
                # the same MCP server (e.g. two email accounts).
                identity_hints = []
                for k, v in (env or {}).items():
                    k_lower = k.lower()
                    if any(x in k_lower for x in ["email_address", "account", "user", "username"]):
                        identity_hints.append(v)
                identity = ", ".join(identity_hints) if identity_hints else ""

                self._sessions[server_id] = session
                self._stacks[server_id] = stack
                self._tools[server_id] = tools
                self._connections[server_id] = {
                    "status": "connected",
                    "name": name,
                    "transport": "stdio",
                    "tool_count": len(tools),
                    "identity": identity,
                }

                registered = True

            finally:
                if not registered:
                    await stack.aclose()

            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via stdio")
            return True

        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {
                "status": "error",
                "error": "mcp package not installed",
                "name": name,
            }
            return False

    async def _connect_sse(self, server_id: str, name: str, url: str) -> bool:
        """Connect to an MCP server via SSE transport."""
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            from contextlib import AsyncExitStack

            stack = AsyncExitStack()
            registered = False

            try:
                transport = await stack.enter_async_context(sse_client(url))
                read_stream, write_stream = transport
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

                await session.initialize()
                tools_result = await session.list_tools()

                tools = []
                for tool in tools_result.tools:
                    tools.append({
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                        # MCP tool annotations (readOnlyHint / destructiveHint) drive
                        # plan-mode read-only gating. Absent on many servers, so we
                        # fall back to a name heuristic in mcp_tool_is_readonly().
                        "annotations": getattr(tool, 'annotations', None),
                    })

                self._sessions[server_id] = session
                self._stacks[server_id] = stack
                self._tools[server_id] = tools
                self._connections[server_id] = {
                    "status": "connected",
                    "name": name,
                    "transport": "sse",
                    "tool_count": len(tools),
                }

                registered = True

                logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via SSE")
                return True

            finally:
                if not registered:
                    await stack.aclose()

        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False

    async def _start_http_connect(self, server_id: str, name: str, url: str, wait: float = 8.0) -> bool:
        """Begin a Streamable HTTP connect in the background. Returns within
        `wait` seconds: True if it connected (cached-token path), otherwise the
        flow is awaiting browser authorization and status becomes 'needs_auth'."""
        import asyncio
        # The background connect deliberately runs outside this server's lock:
        # an OAuth browser flow can take minutes, and holding the lock for that
        # long would block disconnects. disconnect_server() cancels the task
        # instead (see self._connect_tasks).
        self._connections[server_id] = {"status": "connecting", "name": name, "transport": "http"}
        task = asyncio.create_task(self._connect_http(server_id, name, url))
        self._connect_tasks[server_id] = task
        done, _ = await asyncio.wait({task}, timeout=wait)
        if task in done:
            try:
                return task.result()
            except Exception as e:
                self._connections[server_id] = {"status": "error", "error": str(e), "name": name}
                return False
        # Still running → either awaiting authorization, or discovery/DCR is
        # still in flight. If _on_redirect already published needs_auth+auth_url,
        # leave it; otherwise mark needs_auth (auth_url filled in once it fires).
        from src.mcp_oauth import pop_auth_url
        cur = self._connections.get(server_id, {})
        if cur.get("status") != "needs_auth":
            self._connections[server_id] = {
                "status": "needs_auth", "name": name, "transport": "http",
                "auth_url": pop_auth_url(server_id),
            }
        return False

    async def _connect_http(self, server_id: str, name: str, url: str) -> bool:
        """Connect to a Streamable HTTP MCP server (with automatic OAuth)."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            from contextlib import AsyncExitStack
            from src.mcp_oauth import build_provider, clear_auth_url

            def _on_redirect(auth_url):
                # Publish needs_auth the moment the URL is known, independent of
                # how long discovery/DCR took (may exceed the bounded start wait).
                self._connections[server_id] = {
                    "status": "needs_auth", "name": name, "transport": "http",
                    "auth_url": auth_url,
                }

            provider = build_provider(server_id, url, on_redirect=_on_redirect)
            stack = AsyncExitStack()
            transport = await stack.enter_async_context(streamablehttp_client(url, auth=provider))
            read_stream, write_stream, _get_session_id = transport
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            tools_result = await session.list_tools()
            tools = []
            for tool in tools_result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                })

            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._tools[server_id] = tools
            self._connections[server_id] = {
                "status": "connected", "name": name, "transport": "http",
                "tool_count": len(tools),
            }
            clear_auth_url(server_id)
            # Tools changed (this can complete after connect_server already
            # returned, via the background OAuth flow), so bump the generation
            # to invalidate the tool-prompt cache.
            self._generation += 1
            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via http")
            return True
        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False
        except Exception as e:
            desc = _describe_exception(e)
            logger.error(f"Failed to connect HTTP MCP server {name} ({server_id}): {desc}")
            self._connections[server_id] = {"status": "error", "error": desc, "name": name}
            return False

    async def disconnect_server(self, server_id: str):
        """Disconnect from an MCP server."""
        async with self._get_lock(server_id):
            await self._disconnect_server_unlocked(server_id)

    async def _disconnect_server_unlocked(self, server_id: str, log: bool = True):
        """disconnect_server() body. Callers must hold this server's lock.

        The lock matters here specifically because `await stack.aclose()`
        below is a suspension point that sits *between* popping _stacks and
        popping _sessions/_tools/_connections. Unlocked, a connect that
        completed during that await would publish a fresh "connected" entry
        which this call then wiped on the way out, leaving the UI showing a
        disconnected server with 0 tools.
        """
        # Cancel any in-flight HTTP/OAuth background connect so it stops
        # publishing status for a server that may be getting deleted.
        task = self._connect_tasks.pop(server_id, None)
        if task is not None and not task.done():
            task.cancel()
        try:
            from src.mcp_oauth import clear_auth_url
            clear_auth_url(server_id)
        except Exception:
            pass

        stack = self._stacks.pop(server_id, None)
        if stack:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning(f"Error closing MCP server {server_id}: {e}")

        self._sessions.pop(server_id, None)
        self._tools.pop(server_id, None)
        self._connections.pop(server_id, None)
        self._configs.pop(server_id, None)
        self._close_stderr_log(server_id)
        self._generation += 1
        if log:
            logger.info(f"MCP server disconnected: {server_id}")

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        ids = list(self._sessions.keys())
        for sid in ids:
            await self.disconnect_server(sid)

    async def restart_server(
        self,
        server_id: str,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
    ) -> bool:
        """Disconnect then reconnect one server, coalescing concurrent calls.

        Both the Settings and Admin MCP panels expose a Reconnect button, and
        a reconnect takes a couple of seconds, so a single user could easily
        have several overlapping POSTs to
        /api/mcp/servers/{id}/reconnect in flight for the same server. Each
        one used to run its own unsynchronized disconnect+connect pair, which
        went wrong two ways:

          * every attempt spawned its own stdio subprocess, and all but the
            last had its AsyncExitStack dropped on the floor when the next
            connect overwrote _stacks[server_id]; the orphans were closed
            later by the GC from an unrelated task, raising anyio's
            "Attempted to exit cancel scope in a different task" RuntimeError.
          * a disconnect that resumed after a *newer* connect had published
            state popped _sessions/_tools/_connections for a connection that
            was actually live, so /api/mcp/servers reported the server as
            disconnected with 0 tools while the agent could still call its
            tools through the session it had already been handed.

        A caller arriving while a restart is running joins that restart and
        gets its result, instead of queueing another teardown of a server
        that is being rebuilt right now.
        """
        loop = asyncio.get_running_loop()
        task = self._restart_tasks.get(server_id)
        if task is None or task.done() or task.get_loop() is not loop:
            task = loop.create_task(
                self._restart_once(
                    server_id=server_id,
                    name=name,
                    transport=transport,
                    command=command,
                    args=args,
                    env=env,
                    url=url,
                )
            )
            self._restart_tasks[server_id] = task
            task.add_done_callback(
                lambda finished, sid=server_id: self._restart_tasks.pop(sid, None)
                if self._restart_tasks.get(sid) is finished
                else None
            )
        # Shielded: a browser that navigates away mid-reconnect cancels its
        # request task, and that must not cancel a restart other callers (or
        # the server's own connection state) depend on.
        return await asyncio.shield(task)

    async def _restart_once(self, server_id: str, **config) -> bool:
        # Runs in its own task, so it must NOT be called from a caller that
        # already holds this server's lock: the lock is re-entrant per task,
        # and this task is not that task.
        async with self._get_lock(server_id):
            await self._disconnect_server_unlocked(server_id)
            return await self._connect_server_unlocked(server_id=server_id, **config)


    async def connect_all_enabled(self):
        db = SessionLocal()
        try:
            servers = db.query(McpServer).filter(McpServer.is_enabled == True).all()

            tasks = [
                asyncio.create_task(self._connect_with_timeout(srv))
                for srv in servers
            ]

            await asyncio.gather(*tasks)
        finally:
            db.close()


    async def _connect_with_timeout(self, srv):
        args = json.loads(srv.args) if srv.args else []
        env = json.loads(srv.env) if srv.env else {}

        try:
            await asyncio.wait_for(
                self.connect_server(
                    server_id=srv.id,
                    name=srv.name,
                    transport=srv.transport,
                    command=srv.command,
                    args=args,
                    env=env,
                    url=srv.url,
                ),
                timeout=20,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out connecting to %s", srv.name)
            self._connections[srv.id] = {
                "status": "timeout",
                "error": f"Timed out after 20 seconds",
                "name": srv.name,
            }

    async def call_tool(self, qualified_name: str, arguments: Dict) -> Dict:
        """Call an MCP tool by its qualified name (mcp__{server_id}__{tool_name}).

        Returns a result dict compatible with agent_tools format.
        """
        parts = qualified_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return {"error": f"Invalid MCP tool name: {qualified_name}", "exit_code": 1}

        server_id = parts[1]
        tool_name = parts[2]

        session = self._sessions.get(server_id)
        if not session:
            return {"error": f"MCP server not connected: {server_id}", "exit_code": 1}

        try:
            result = await self._do_call(session, tool_name, arguments)
        except Exception as e:
            # Describe the failure *before* reconnecting: restarting the server
            # deletes the dead subprocess's stderr capture, which is usually the
            # only record of why it died.
            detail = self._describe_call_failure(server_id, e)
            # Auto-reconnect servers whose subprocess may have died. Builtins keep
            # their historical blanket retry; user-added servers retry only when
            # the transport is provably gone. Such a call fails in ~1ms before any
            # bytes are written, so replaying it cannot duplicate a side effect.
            # Without this, a user-added stdio server stayed dead until Odysseus
            # restarted -- every call returning exit_code=1 with a messageless
            # anyio error (the ntfy-me-mcp "empty error" symptom).
            if self.is_builtin(server_id) or _is_dead_transport_error(e):
                logger.warning(f"MCP call failed for {qualified_name}, attempting reconnect: {detail}")
                reconnected = await self._reconnect_server(server_id)
                if reconnected:
                    session = self._sessions.get(server_id)
                    if session:
                        try:
                            result = await self._do_call(session, tool_name, arguments)
                        except Exception as e2:
                            desc2 = self._describe_call_failure(server_id, e2)
                            logger.error(f"MCP tool call failed after reconnect: {qualified_name}: {desc2}")
                            return {"error": desc2, "exit_code": 1}
                    else:
                        return {"error": f"Reconnected but no session for {server_id}", "exit_code": 1}
                else:
                    logger.error(f"MCP reconnect failed for {server_id}: {detail}")
                    return {
                        "error": f"MCP server crashed and could not be restarted: {server_id}\n\n{detail}",
                        "exit_code": 1,
                    }
            else:
                logger.error(f"MCP tool call failed: {qualified_name}: {detail}")
                return {"error": detail, "exit_code": 1}

        return result

    async def _do_call(self, session, tool_name: str, arguments: Dict) -> Dict:
        """Execute a single MCP tool call and return result dict."""
        result = await session.call_tool(tool_name, arguments)
        output_parts = []
        images = []
        for content in result.content:
            if hasattr(content, 'text'):
                output_parts.append(content.text)
            elif getattr(content, 'type', '') == 'image' and hasattr(content, 'data'):
                # Image content (e.g. Playwright screenshots)
                mime = getattr(content, 'mimeType', 'image/png')
                images.append({"data": content.data, "mimeType": mime})
                output_parts.append(f"[Screenshot captured ({mime})]")
            elif hasattr(content, 'data'):
                output_parts.append(str(content.data))

        output = "\n".join(output_parts)
        is_error = getattr(result, 'isError', False)

        result_dict = {
            "stdout": output if not is_error else "",
            "stderr": output if is_error else "",
            "exit_code": 1 if is_error else 0,
        }
        if images:
            result_dict["images"] = images
        return result_dict

    def _open_stderr_log(self, server_id: str):
        """Open a fresh stderr capture file for a stdio server's subprocess.

        `stdio_client(errlog=...)` is handed to the child process as its stderr
        file descriptor, so this must be a real file, not an in-memory buffer.
        Returns None (and falls back to inherited stderr) if the file can't be
        created -- capturing diagnostics must never block a connection.

        Builtin servers are deliberately excluded: some of them (e.g. lotus)
        stream INFO logs to stderr so they show up in the app's own container
        logs, and redirecting that into a temp file would hide it. Their code is
        ours to debug, whereas a third-party npx server's dying words are often
        the only clue available.
        """
        import tempfile

        self._close_stderr_log(server_id)
        if self.is_builtin(server_id):
            return None
        try:
            handle = tempfile.NamedTemporaryFile(
                mode="w+", prefix=f"mcp-{server_id}-", suffix=".stderr.log", delete=False
            )
            self._stderr_logs[server_id] = {"path": handle.name, "handle": handle}
            return handle
        except Exception as e:
            logger.debug(f"Could not capture stderr for MCP server {server_id}: {e}")
            return None

    def _read_stderr_tail(self, server_id: str) -> str:
        """Return the tail of a stdio server's captured stderr, or ""."""
        entry = self._stderr_logs.get(server_id)
        if not entry:
            return ""
        try:
            size = os.path.getsize(entry["path"])
            with open(entry["path"], "r", encoding="utf-8", errors="replace") as fh:
                if size > _STDERR_TAIL_BYTES:
                    fh.seek(size - _STDERR_TAIL_BYTES)
                return fh.read().strip()
        except Exception:
            return ""

    def _close_stderr_log(self, server_id: str):
        """Close and remove a stdio server's stderr capture file."""
        entry = self._stderr_logs.pop(server_id, None)
        if not entry:
            return
        try:
            entry["handle"].close()
        except Exception:
            pass
        try:
            os.unlink(entry["path"])
        except Exception:
            pass

    def _describe_call_failure(self, server_id: str, error: BaseException) -> str:
        """Turn a tool-call exception into something a user can act on.

        The bare exception is frequently useless -- anyio raises a messageless
        ClosedResourceError when the server's pipe is gone -- so name the server,
        say the subprocess exited, and quote whatever it printed on the way out.
        """
        desc = _describe_exception(error)
        if not _is_dead_transport_error(error):
            return desc

        config = self._configs.get(server_id, {})
        conn = self._connections.get(server_id, {})
        name = config.get("name") or conn.get("name") or server_id
        command_line = " ".join(
            [config.get("command") or "", *(config.get("args") or [])]
        ).strip()

        lines = [
            f"MCP server '{name}' ({server_id}) is not running — its connection is closed, "
            f"so the tool call never reached it ({desc})."
        ]
        if command_line:
            lines.append(f"Command: {command_line}")
        tail = self._read_stderr_tail(server_id)
        if tail:
            lines.append(f"Last output from the server:\n{tail}")
        else:
            lines.append(
                "The server produced no output before exiting — check its configuration "
                "(command, args, and env such as URL/token/topic values)."
            )
        return "\n\n".join(lines)

    async def _reconnect_server(self, server_id: str) -> bool:
        """Restart a crashed MCP server using the config it was connected with.

        Builtins keep their dedicated path (their command is derived, not stored).
        Only stdio servers are restarted automatically: HTTP/SSE reconnects can
        re-enter the OAuth browser flow, which must not happen inside a tool call.

        Holds the server's lock across the whole teardown+rebuild so a
        crash-recovery restart and a user-triggered reconnect cannot run at
        the same time. The lock is re-entrant within this task, which is why
        the public disconnect_server()/connect_server() can still be used
        below.
        """
        async with self._get_lock(server_id):
            if self.is_builtin(server_id):
                return await self._reconnect_builtin(server_id)

            # Snapshot before disconnect_server() clears it.
            config = dict(self._configs.get(server_id) or {})
            if not config:
                logger.warning(f"No stored config to reconnect MCP server: {server_id}")
                return False
            if config.get("transport") != "stdio":
                return False

            await self.disconnect_server(server_id)
            try:
                ok = await self.connect_server(**config)
                if ok:
                    logger.info(f"Reconnected MCP server: {config.get('name', server_id)} ({server_id})")
                return ok
            except Exception as e:
                logger.error(f"Failed to reconnect MCP server {server_id}: {_describe_exception(e)}")
                return False

    async def _reconnect_builtin(self, server_id: str) -> bool:
        """Tear down and reconnect a crashed builtin MCP server."""
        import sys
        from src.builtin_mcp import _BUILTIN_SERVERS, builtin_python_env

        if server_id not in _BUILTIN_SERVERS:
            return False

        script_rel, name = _BUILTIN_SERVERS[server_id]
        base_dir = get_app_root()
        script_path = os.path.join(base_dir, script_rel)

        async with self._get_lock(server_id):
            # Clean up old connection
            await self.disconnect_server(server_id)

            try:
                ok = await self.connect_server(
                    server_id=server_id,
                    name=name,
                    transport="stdio",
                    command=sys.executable,
                    args=[script_path],
                    env=builtin_python_env(base_dir),
                )
                if ok:
                    logger.info(f"Reconnected builtin MCP server: {name}")
                return ok
            except Exception as e:
                logger.error(f"Failed to reconnect builtin MCP server {name}: {_describe_exception(e)}")
                return False

    def get_all_openai_schemas(self, disabled_map: Optional[Dict[str, set]] = None) -> List[Dict]:
        """Return all MCP tools in OpenAI function-calling format.

        Tool names are namespaced as mcp__{server_id}__{tool_name}.
        disabled_map: optional {server_id: set_of_disabled_tool_names} to filter out.
        """
        schemas = []
        for server_id, tools in self._tools.items():
            # Skip builtin Python servers — they use the code-block tool format
            # But include NPX-based builtins (like browser) which need function calling
            if self.is_builtin(server_id) and server_id not in _BUILTIN_FUNCTION_CALLING_SERVERS:
                continue
            conn = self._connections.get(server_id, {})
            server_name = conn.get("name", server_id)
            disabled = (disabled_map or {}).get(server_id, set())

            identity = conn.get("identity", "")
            label = f"{server_name} ({identity})" if identity else server_name

            for tool in tools:
                if tool["name"] in disabled:
                    continue
                qualified = f"mcp__{server_id}__{tool['name']}"
                schema = {
                    "type": "function",
                    "function": {
                        "name": qualified,
                        "description": f"[MCP:{label}] {tool['description']}",
                        "parameters": _model_visible_schema(tool.get("input_schema")),
                    },
                }
                schemas.append(schema)

        return schemas

    def gated_tool_names(self) -> Set[str]:
        """Qualified names of MCP tools that must still win RAG/intent-based
        tool selection to appear in a turn's schema list.

        These are the large, embedded catalogs (the browser, GitHub, Todoist,
        Lotus, pi_worker connectors) where showing every tool on every turn
        would flood small models with ~30 irrelevant schemas (issue tracked
        alongside the Terminus toolset swap).

        Every other connected server is one the user explicitly added via
        MCP settings, is typically a handful of tools, and must remain
        callable on every turn regardless of how the RAG tool-selection
        heuristic scores that turn's wording -- otherwise a real, connected
        tool silently disappears from the schema the moment a follow-up
        message ("continue", "now run it") does not semantically resemble
        its description. See root-cause note in agent_loop._tool_schemas_for_round.
        """
        names: Set[str] = set()
        for server_id, tools in self._tools.items():
            if server_id not in _BUILTIN_FUNCTION_CALLING_SERVERS:
                continue
            for tool in tools:
                names.add(f"mcp__{server_id}__{tool['name']}")
        return names

    def get_all_tools(self, disabled_map: Optional[Dict[str, set]] = None) -> List[Dict]:
        """Return a flat list of all discovered tools with server info."""
        result = []
        for server_id, tools in self._tools.items():
            conn = self._connections.get(server_id, {})
            disabled = (disabled_map or {}).get(server_id, set())
            for tool in tools:
                result.append({
                    "server_id": server_id,
                    "server_name": conn.get("name", server_id),
                    "name": tool["name"],
                    "qualified_name": f"mcp__{server_id}__{tool['name']}",
                    "description": tool.get("description", ""),
                    "input_schema": _model_visible_schema(tool.get("input_schema")),
                    "is_disabled": tool["name"] in disabled,
                })
        return result

    def plan_mode_blocked_mcp(self) -> Tuple[Dict[str, Set[str]], Set[str]]:
        """Plan mode: block every MCP tool that isn't clearly read-only.

        Returns (disabled_map, qualified_names):
          - disabled_map: {server_id: {tool_name, ...}} to hide write tools from
            the prompt/schemas (merged into the existing mcp_disabled_map).
          - qualified_names: {"mcp__<server>__<tool>", ...} for runtime rejection
            in execute_tool_block (which matches the qualified name).
        """
        disabled_map: Dict[str, Set[str]] = {}
        qualified: Set[str] = set()
        for server_id, tools in self._tools.items():
            for tool in tools:
                if not mcp_tool_is_readonly(tool):
                    disabled_map.setdefault(server_id, set()).add(tool["name"])
                    qualified.add(f"mcp__{server_id}__{tool['name']}")
        return disabled_map, qualified

    def is_builtin(self, server_id: str) -> bool:
        """Check if a server is a built-in (auto-registered) server."""
        return server_id.startswith("builtin_") or server_id in {
            "image_gen",
            "memory",
            "rag",
            "email",
            "todoist",
            "lotus",
            "pi_worker",
            "github_read",
            "github_write",
        }

    def get_server_status(self, server_id: str) -> Dict:
        """Get connection status for a server."""
        return self._connections.get(server_id, {"status": "disconnected"})

    def get_all_statuses(self) -> Dict[str, Dict]:
        """Get connection statuses for all servers."""
        return dict(self._connections)

    _cached_prompt_desc = None
    _cached_prompt_desc_key = None

    def get_tool_descriptions_for_prompt(self, disabled_map: Optional[Dict[str, set]] = None) -> str:
        """Generate text describing MCP tools for the agent system prompt. Cached."""
        cache_key = (
            frozenset((k, frozenset(v)) for k, v in (disabled_map or {}).items()),
            len(self._tools),
            self._generation,
        )
        if self._cached_prompt_desc is not None and self._cached_prompt_desc_key == cache_key:
            return self._cached_prompt_desc
        tools = self.get_all_tools(disabled_map)
        if not tools:
            return ""

        lines = ["\n\nYou also have access to external MCP tool servers. These tools are called via native function calling:"]
        by_server = {}
        for t in tools:
            # Skip builtin Python servers — they're already in the agent prompt
            # But include NPX-based builtins (like browser) which aren't hardcoded
            if self.is_builtin(t["server_id"]) and t["server_id"] not in _BUILTIN_FUNCTION_CALLING_SERVERS:
                continue
            if t.get("is_disabled"):
                continue
            sn = t["server_name"]
            if sn not in by_server:
                by_server[sn] = []
            by_server[sn].append(t)

        if not by_server:
            return ""

        for server_name, server_tools in by_server.items():
            # Include identity (e.g. email address) if available
            sid = server_tools[0]["server_id"] if server_tools else ""
            identity = self._connections.get(sid, {}).get("identity", "")
            label = f"{server_name} ({identity})" if identity else server_name
            lines.append(f"\n**{label}:**")
            for t in server_tools:
                # One line per tool, truncated. A multi-line description
                # ("Actions:\n- create: ...") otherwise reads as extra
                # "- name: desc" rows; the tool index registered a phantom
                # `create` tool from one (2026-09-13 logs).
                flat = " ".join(str(t.get('description') or '').split())
                desc = flat[:120] + '...' if len(flat) > 120 else flat
                # Include the tool's declared inputs so the model calls it with
                # real argument names instead of guessing from the description
                # alone (issue #2509).
                args_hint = _format_mcp_params(t.get("input_schema"))
                lines.append(f"  - {t['qualified_name']}: {desc}{args_hint}")

        result = "\n".join(lines)
        self._cached_prompt_desc = result
        self._cached_prompt_desc_key = cache_key
        return result
