"""Gateway-backed MCP transport for :class:`~src.mcp_manager.McpManager`.

An MCP API gateway does not speak the MCP wire protocol — it serves a JSON
route map at its origin (``{"endpoints": {"tools": "/ai/tools", ...}}``) and
plain HTTP responses underneath. So this module adapts one to the other:

* discovery reads the manifest and synthesises a tool per advertised route,
  rather than calling ``list_tools``;
* a call POSTs to the resolved path through :class:`~src.mcp_gateway.GatewayPool`,
  so it inherits round-robin selection, exponential backoff and the dead list.

The pool is consulted per call, not per session. A gateway added or revived at
runtime is picked up by the next call without reconnecting.

Result shape matches what ``McpManager._do_call`` expects: ``call_tool``
returns an object exposing ``.content`` (items with ``.text``) and ``.isError``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Manifest routes that are safe to expose as agent tools. Health/metrics/vitals
# are observability endpoints: calling them would burn a model turn to return
# numbers the operator already has on the dashboard.
CALLABLE_ROUTES = ("tools", "route", "compose", "chat", "osint_analyze", "models")

# Routes deliberately excluded even though they appear in a manifest.
EXCLUDED_ROUTES = frozenset({
    "health", "metrics", "vitals", "abilities",
    "mcp_resources", "mcp_read", "capabilities",
})

_DESCRIPTIONS = {
    "tools": "List tools the gateway exposes.",
    "route": "Ask the gateway to route a request to the best available backend.",
    "compose": "Compose a multi-step request across gateway capabilities.",
    "chat": "Send a prompt through the gateway to its configured chat backend.",
    "osint_analyze": "Visual GEOINT geolocation of an uploaded image.",
    "models": "List models the gateway can route to.",
}


class _Text:
    """MCP-shaped text content item."""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class _Result:
    """MCP-shaped call result."""

    __slots__ = ("content", "isError")

    def __init__(self, content: List[_Text], isError: bool = False):
        self.content = content
        self.isError = isError


class GatewaySession:
    """Session object stored in ``McpManager._sessions`` for gateway servers.

    Deliberately not an ``AsyncExitStack`` user: there is no subprocess and no
    long-lived stream, so ``disconnect_server`` has nothing to tear down.
    """

    def __init__(self, server_id: str, name: str, manifest: Dict[str, str]):
        self.server_id = server_id
        self.name = name
        self.manifest = dict(manifest)
        self.tools = build_tools(self.manifest)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> _Result:
        from src.mcp_gateway import GatewayError, GatewayExhausted, GatewayRejected, get_pool

        path = self.manifest.get(tool_name)
        if not path:
            return _Result([_Text(f"Gateway has no route named {tool_name!r}")], True)

        pool = get_pool()
        if not pool.endpoints:
            return _Result(
                [_Text("No gateway endpoint is configured (ODYSSEUS_GATEWAY_URLS is empty)")],
                True,
            )

        try:
            # arguments go as the JSON body. A gateway route that expects
            # multipart cannot be reached this way; those need a dedicated
            # tool (see src/agent_tools/osint_tools.py for osint_analyze).
            payload = await pool.request(
                path, method="POST", json_body=arguments or {}
            )
        except GatewayExhausted as exc:
            return _Result([_Text(f"Gateway unavailable: {exc}")], True)
        except GatewayRejected as exc:
            return _Result([_Text(f"Gateway rejected the request: {exc}")], True)
        except GatewayError as exc:
            return _Result([_Text(f"Gateway error: {exc}")], True)
        except Exception as exc:
            return _Result([_Text(f"Gateway call failed: {exc}")], True)

        return _Result([_Text(_render(payload))])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _render(payload: Any) -> str:
    """Render a gateway response for the model.

    Truncated: a gateway route can return a large listing, and an unbounded
    result would eat the turn's context budget.
    """
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = str(payload)
    if len(text) > 8000:
        text = text[:8000] + f"\n[truncated, {len(text)} bytes total]"
    return text


def build_tools(manifest: Dict[str, str]) -> List[Dict[str, Any]]:
    """Synthesise tool descriptors from a gateway manifest."""
    tools: List[Dict[str, Any]] = []
    for key in CALLABLE_ROUTES:
        if key not in manifest or key in EXCLUDED_ROUTES:
            continue
        tools.append({
            "name": key,
            "description": _DESCRIPTIONS.get(key, f"Call the gateway route {key!r}."),
            # Free-form: the body is forwarded verbatim, and the gateway
            # validates it. A fabricated schema here would be a guess.
            "input_schema": {"type": "object", "additionalProperties": True},
            # Unknown side effects. Marking these read-only would let plan mode
            # run a `compose` or `route` call, which can mutate upstream state.
            "annotations": None,
        })
    return tools


async def connect_gateway(server_id: str, name: str) -> bool:
    """Discover the gateway manifest and install a session.

    Returns True when at least one callable route was found. A gateway with no
    callable routes is treated as a failed connect rather than a connected
    server with zero tools, so the UI shows the error instead of silently
    offering nothing.
    """
    from src.mcp_gateway import GatewayError, GatewayExhausted, get_pool

    pool = get_pool()
    if not pool.endpoints:
        logger.warning("MCP gateway %s: no endpoints configured", name)
        return False

    manifest: Dict[str, str] = {}
    errors: List[str] = []
    # Probe each routable endpoint until one answers; discovery is best-effort
    # and must not fail the whole connect when one gateway is down.
    for endpoint in pool.endpoints:
        try:
            payload = await pool.discover(endpoint.base_url)
        except GatewayExhausted:
            errors.append(f"{endpoint.base_url}: all attempts exhausted")
            continue
        except GatewayError as exc:
            errors.append(f"{endpoint.base_url}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{endpoint.base_url}: {type(exc).__name__}: {exc}")
            continue
        advertised = payload.get("endpoints")
        if isinstance(advertised, dict):
            manifest = {k: v for k, v in advertised.items() if isinstance(v, str)}
            break

    if not manifest:
        logger.warning("MCP gateway %s: no manifest discovered (%s)", name, "; ".join(errors))
        return False

    session = GatewaySession(server_id, name, manifest)
    if not session.tools:
        logger.warning(
            "MCP gateway %s: manifest advertised no callable routes (had: %s)",
            name, ", ".join(sorted(manifest)),
        )
        return False
    return session