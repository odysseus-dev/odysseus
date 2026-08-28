"""
mcp_manager.py

Manages connections to MCP (Model Context Protocol) tool servers.
Each server exposes tools that are made available to the agent loop.
"""

import json
import logging
import os
import re
import time
import asyncio 
from typing import Any, Dict, List, Optional, Set, Tuple
from src.database import McpServer, SessionLocal

from src.runtime_paths import get_app_root

logger = logging.getLogger(__name__)

def _format_mcp_connection_error(name: str, command: str = "", args: Optional[List[str]] = None, error: Exception = None) -> str:
    """Return a user-actionable MCP connection error message."""
    args = args or []
    raw_error = str(error) if error else "Unknown error"
    command_line = " ".join([command or "", *args]).strip()
    lower_command = command_line.lower()

    if "@playwright/mcp" in lower_command:
        return (
            f"{raw_error}\n\n"
            "Browser MCP could not start. On fresh installs, cache the Playwright MCP package once before connecting:\n\n"
            "npx -y @playwright/mcp@latest --version\n\n"
            "Then restart Odysseus and reconnect the Browser MCP server."
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


# A reconnect must not hang a caller forever on a stalled transport, and must
# not be attempted concurrently for the same server (see _reconnect_any).
# The bound covers one attempt; a transport may add its own teardown grace.
_MCP_RECONNECT_TIMEOUT = 30.0
# After a failed recovery, don't re-attempt on every single tool call: a
# permanently dead server would make each call pay the full timeout.
_MCP_RECONNECT_COOLDOWN = 15.0


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
        # server_id -> original connect_server kwargs, kept so a user-added
        # server whose session died can be reconnected (issue #1789)
        self._connect_params: Dict[str, Dict[str, Any]] = {}
        # server_id -> session identity, bumped on every connect AND disconnect.
        # A caller records the epoch it used so a stale in-flight failure can
        # never act on (or resurrect) a session that has since been replaced,
        # explicitly disabled, or deleted.
        self._session_epoch: Dict[str, int] = {}
        # server_id -> lock, so concurrent failures coalesce onto ONE reconnect
        # instead of racing to spawn duplicate processes.
        self._reconnect_locks: Dict[str, asyncio.Lock] = {}
        # Servers taken down explicitly (disable/delete/shutdown). Auto-recovery
        # must never resurrect these — including builtins, which carry no
        # user-supplied connect params to revoke.
        self._revoked: Set[str] = set()
        # server_id -> monotonic timestamp of the last failed recovery.
        self._reconnect_failed_at: Dict[str, float] = {}
        # Tracking updates to tools/connections for RAG indexing / prompt cache
        self._generation = 0

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
        self._connect_params[server_id] = {
            "name": name,
            "transport": transport,
            "command": command,
            "args": list(args or []),
            "env": dict(env or {}),
            "url": url,
        }
        # An explicit connect request is the un-revoke signal: clear it here,
        # not on success, so a failed first attempt cannot leave the server
        # permanently ineligible for automatic recovery.
        self._revoked.discard(server_id)
        self._reconnect_failed_at.pop(server_id, None)
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
                self._session_epoch[server_id] = self._session_epoch.get(server_id, 0) + 1
                self._generation += 1
            return res
        except Exception as e:
            logger.error(f"Failed to connect MCP server {name} ({server_id}): {e}")
            error_message = _format_mcp_connection_error(name, command or "", args or [], e)
            self._connections[server_id] = {"status": "error", "error": error_message, "name": name}
            self._generation += 1
            return False

    async def _connect_stdio(self, server_id: str, name: str, command: str, args: List[str], env: Dict[str, str]) -> bool:
        """Connect to an MCP server via stdio transport."""
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
                    "annotations": getattr(tool, "annotations", None),
                })

            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._tools[server_id] = tools
            self._connections[server_id] = {
                "status": "connected", "name": name, "transport": "http",
                "tool_count": len(tools),
            }
            # This can complete long after connect_server returned (background
            # OAuth), so do the session accounting here too — otherwise the
            # epoch guard would not see this session at all.
            self._session_epoch[server_id] = self._session_epoch.get(server_id, 0) + 1
            self._revoked.discard(server_id)
            self._reconnect_failed_at.pop(server_id, None)
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
            logger.error(f"Failed to connect HTTP MCP server {name} ({server_id}): {e}")
            self._connections[server_id] = {"status": "error", "error": str(e), "name": name}
            return False

    async def disconnect_server(self, server_id: str, *, revoke_params: bool = True):
        """Disconnect from an MCP server.

        `revoke_params=True` (the default, i.e. an explicit disable/delete)
        also drops the cached connect parameters so a late in-flight call
        cannot silently respawn the server from that snapshot. Internal
        recycling (reconnect) passes False to keep the config for the retry.
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
        if revoke_params:
            self._connect_params.pop(server_id, None)
            self._revoked.add(server_id)
        # Invalidate the session identity either way: a caller holding the old
        # epoch must not treat the next session as the one it was using.
        self._session_epoch[server_id] = self._session_epoch.get(server_id, 0) + 1
        self._generation += 1
        logger.info(f"MCP server disconnected: {server_id}")

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        ids = list(self._sessions.keys())
        for sid in ids:
            await self.disconnect_server(sid)


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

        # Record which session generation this call uses, so a failure can
        # never act on a session that was replaced or revoked meanwhile.
        epoch = self._session_epoch.get(server_id, 0)
        # Snapshot the schema now: a reconnect clears the live tool table, and
        # replay safety must not fall back to "unknown tool" because of that.
        tool_schema = self._find_tool_schema(server_id, tool_name)
        session = self._sessions.get(server_id)
        if not session:
            # An earlier reconnect may have failed after tearing the session
            # down. Retry once from the cached config so one transient failure
            # doesn't strand the server until a manual reconnect (#1789).
            # expected_epoch matters here too: without it this path skips the
            # single-flight check, and concurrent callers each run a full
            # reconnect that tears down the session a peer just restored.
            # Only for servers we actually know: the tool name is model-supplied,
            # so an invented id must not allocate recovery state.
            known = self.is_builtin(server_id) or server_id in self._connect_params
            if known and await self._reconnect_any(server_id, expected_epoch=epoch):
                epoch = self._session_epoch.get(server_id, 0)
                session = self._sessions.get(server_id)
                # The tool table was empty before the reconnect, so re-resolve
                # the schema — otherwise an annotated read-only tool would be
                # reported as having an unknown outcome.
                if tool_schema is None:
                    tool_schema = self._find_tool_schema(server_id, tool_name)
            if not session:
                return {"error": f"MCP server not connected: {server_id}", "exit_code": 1}

        try:
            result = await self._do_call(session, tool_name, arguments)
        except Exception as e:
            # The session may have died — a builtin subprocess crash, or a
            # stdio session whose creating asyncio task has exited (#1789).
            # Reconnect so later calls succeed, but only REPLAY this call when
            # that cannot duplicate a side effect: a transport error can arrive
            # after the server already committed the operation.
            logger.warning(f"MCP call failed for {qualified_name}, attempting reconnect: {e}")
            replay_safe = self._replay_is_safe(tool_schema)
            if not await self._reconnect_any(server_id, expected_epoch=epoch):
                logger.error(f"MCP reconnect failed for {server_id}")
                return {"error": f"MCP server crashed and reconnect failed: {server_id}", "exit_code": 1}
            session = self._sessions.get(server_id)
            if not session:
                return {"error": f"Reconnected but no session for {server_id}", "exit_code": 1}
            if not replay_safe:
                logger.warning(
                    f"Not replaying {qualified_name} after reconnect: the tool is not "
                    f"declared read-only/idempotent, so its outcome is indeterminate"
                )
                return {
                    "error": (
                        f"MCP call '{tool_name}' failed in flight and its outcome is UNKNOWN — "
                        "the server may have applied it before the connection dropped. The "
                        "session was reconnected, but the call was not replayed automatically "
                        "because the tool is not declared read-only or idempotent. Check the "
                        "server's state before retrying."
                    ),
                    "exit_code": 1,
                    "indeterminate": True,
                }
            try:
                result = await self._do_call(session, tool_name, arguments)
            except Exception as e2:
                logger.error(f"MCP tool call failed after reconnect: {qualified_name}: {e2}")
                return {"error": str(e2), "exit_code": 1}

        return result

    def _find_tool_schema(self, server_id: str, tool_name: str) -> Optional[Dict]:
        """Return the advertised schema for a tool, or None if unknown."""
        for tool in self._tools.get(server_id) or []:
            if (tool.get("name") or "") == tool_name:
                return tool
        return None

    @staticmethod
    def _replay_is_safe(tool: Optional[Dict]) -> bool:
        """May a failed call be transparently replayed after a reconnect?

        Only when the SERVER ITSELF declares a read-only or idempotent
        contract (MCP `readOnlyHint` / `idempotentHint`). A transport error can
        arrive AFTER the server committed the write, so replaying anything else
        can execute a user-visible action (send, create, delete, ...) twice
        while the caller sees only one result.

        The name-verb heuristic used for plan mode is deliberately NOT accepted
        here: names like `get_or_create_issue` read as "get" but mutate, and a
        guess is not a contract. Fails closed — unknown or unannotated tools
        are never replayed.
        """
        if not tool:
            return False
        ann = tool.get("annotations")
        if ann is None:
            return False
        if isinstance(ann, dict):
            read_hint = ann.get("readOnlyHint")
            idempotent = ann.get("idempotentHint")
            destructive = ann.get("destructiveHint")
        else:
            read_hint = getattr(ann, "readOnlyHint", None)
            idempotent = getattr(ann, "idempotentHint", None)
            destructive = getattr(ann, "destructiveHint", None)
        # Contradictory annotations (read-only AND destructive) are malformed —
        # fail closed unless the server also promises idempotency, which by
        # definition makes a repeat harmless.
        if destructive is True and idempotent is not True:
            return False
        return read_hint is True or idempotent is True

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
        if is_error and output:
            result_dict["untrusted_content"] = True
        if images:
            result_dict["images"] = images
        return result_dict

    async def _reconnect_any(self, server_id: str, *, expected_epoch: Optional[int] = None) -> bool:
        """Tear down and reconnect any MCP server, builtin or user-added.

        Serialized per server: concurrent failures coalesce onto ONE recovery
        instead of racing to spawn duplicate processes and overwrite the shared
        session/cleanup slots. `expected_epoch` pins the recovery to the
        session the caller actually used — if it changed while we waited for
        the lock (another caller recovered, or an admin disabled/deleted the
        server), we adopt that outcome instead of reconnecting on top of it.

        One attempt is bounded by _MCP_RECONNECT_TIMEOUT (plus the transport's
        own teardown grace), and a failure starts a short cooldown so a dead
        server doesn't make every later tool call pay that timeout.
        """
        lock = self._reconnect_locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            if expected_epoch is not None and self._session_epoch.get(server_id, 0) != expected_epoch:
                # Someone else already resolved this server's fate while we
                # waited for the lock — adopt their outcome instead of
                # reconnecting on top of it.
                return self._sessions.get(server_id) is not None
            last_failure = self._reconnect_failed_at.get(server_id)
            if last_failure is not None and (time.monotonic() - last_failure) < _MCP_RECONNECT_COOLDOWN:
                # A permanently dead server would otherwise make every tool
                # call pay the full reconnect timeout.
                logger.debug(f"Skipping MCP reconnect for {server_id}: in cooldown")
                return False
            try:
                # asyncio.timeout (not wait_for): wait_for runs the coroutine in
                # a NEW task, which would close the transport's anyio cancel
                # scope from a different task than the one that entered it —
                # the very failure mode this reconnect exists to recover from.
                async with asyncio.timeout(_MCP_RECONNECT_TIMEOUT):
                    ok = await self._do_reconnect(server_id)
            except asyncio.TimeoutError:
                logger.error(
                    f"MCP reconnect for {server_id} timed out after "
                    f"{_MCP_RECONNECT_TIMEOUT:.0f}s; closing partial connection"
                )
                # Close whatever the cancelled connect left behind, but keep
                # the config so a later call can retry.
                await self.disconnect_server(server_id, revoke_params=False)
                ok = False
            if not ok:
                self._reconnect_failed_at[server_id] = time.monotonic()
            return ok

    async def _do_reconnect(self, server_id: str) -> bool:
        """One reconnect attempt. Callers hold the per-server lock."""
        if server_id in self._revoked:
            # Explicitly disabled, deleted, or shut down: an in-flight call
            # that failed afterwards must not respawn it. Applies to builtins
            # too, which have no user-supplied params to revoke.
            logger.info(f"Not reconnecting MCP server {server_id}: explicitly disconnected")
            return False
        if self.is_builtin(server_id):
            return await self._reconnect_builtin(server_id)
        params = self._connect_params.get(server_id)
        if not params:
            # Never connected, or explicitly disabled/deleted (params revoked)
            # — do not resurrect a server from a stale snapshot.
            logger.error(f"No stored connect params to reconnect MCP server {server_id}")
            return False
        await self.disconnect_server(server_id, revoke_params=False)
        try:
            ok = await self.connect_server(server_id=server_id, **params)
            if ok:
                logger.info(f"Reconnected user MCP server: {params.get('name', server_id)}")
            return ok
        except Exception as e:
            logger.error(f"Failed to reconnect MCP server {server_id}: {e}")
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

        # Clean up old connection (internal recycle — keep the config)
        await self.disconnect_server(server_id, revoke_params=False)

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
            logger.error(f"Failed to reconnect builtin MCP server {name}: {e}")
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
            if self.is_builtin(server_id) and server_id != "builtin_browser":
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
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                schemas.append(schema)

        return schemas

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
                    "input_schema": tool.get("input_schema") or {},
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
            if self.is_builtin(t["server_id"]) and t["server_id"] != "builtin_browser":
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
                # Truncate long descriptions
                desc = t['description'][:120] + '...' if len(t['description']) > 120 else t['description']
                # Include the tool's declared inputs so the model calls it with
                # real argument names instead of guessing from the description
                # alone (issue #2509).
                args_hint = _format_mcp_params(t.get("input_schema"))
                lines.append(f"  - {t['qualified_name']}: {desc}{args_hint}")

        result = "\n".join(lines)
        self._cached_prompt_desc = result
        self._cached_prompt_desc_key = cache_key
        return result
