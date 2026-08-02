"""Bounded MCP client bridge to the independent PDV Execution OS service."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


server = Server("pdv-control")
_TOOL_MAP = {
    "pdv_health_status": "health.status",
    "pdv_memory_read": "memory.read",
    "pdv_memory_write": "memory.write",
    "pdv_memory_delete": "memory.delete",
    "pdv_dispatch_submit": "dispatch.submit",
    "pdv_dispatch_status": "dispatch.status",
    "pdv_dispatch_cancel": "dispatch.cancel",
}


def _boundary() -> tuple[str, str]:
    base = os.environ.get("PDV_EXECUTION_OS_URL", "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        raise RuntimeError("PDV Execution OS requires an explicit loopback HTTP URL and port")
    key_path = Path(os.environ.get("ODYSSEUS_PDV_ADAPTER_KEY_FILE", "").strip())
    try:
        key = key_path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise RuntimeError("PDV adapter credential reference is unavailable") from error
    if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
        raise RuntimeError("PDV adapter credential format is invalid")
    return base, key


async def _invoke(tool_name: str, arguments: dict) -> dict:
    base, key = _boundary()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
        response = await client.post(
            f"{base}/v1/integrations/odysseus/mcp/invoke",
            headers={"X-PDV-Odysseus-Key": key},
            json={"server_id": "pdv-control", "tool_name": tool_name, "arguments": arguments},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        reason = payload.get("reason_code") if isinstance(payload, dict) else None
        raise RuntimeError(f"PDV MCP invocation refused ({response.status_code}, {reason or 'UNAVAILABLE'})")
    if not isinstance(payload, dict) or payload.get("allowed") is not True or "provenance" not in payload:
        raise RuntimeError("PDV MCP response failed provenance validation")
    return payload


@server.list_tools()
async def list_tools() -> list[Tool]:
    source = {
        "source": {"type": "object"},
        "retention": {"type": "object"},
    }
    return [
        Tool(name="pdv_health_status", description="Read governed PDV Execution OS health and kill-switch status.", inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
        Tool(name="pdv_memory_read", description="Read one provenance-tracked Odysseus memory record from PDV.", inputSchema={"type": "object", "properties": {"memory_id": {"type": "string"}}, "required": ["memory_id"], "additionalProperties": False}),
        Tool(name="pdv_memory_write", description="Write one provenance-tracked Odysseus memory record when PDV policy authorizes mutation.", inputSchema={"type": "object", "properties": {"memory_id": {"type": "string"}, "owner": {"type": "string"}, "materialization": {"type": "string"}, "content": {"type": "string"}, **source}, "required": ["memory_id", "owner", "materialization", "content", "source", "retention"], "additionalProperties": False}),
        Tool(name="pdv_memory_delete", description="Delete one Odysseus-owned memory record when PDV policy authorizes mutation.", inputSchema={"type": "object", "properties": {"memory_id": {"type": "string"}}, "required": ["memory_id"], "additionalProperties": False}),
        Tool(name="pdv_dispatch_submit", description="Submit bounded work to the governed PDV Execution OS. Recursive dispatch is denied.", inputSchema={"type": "object", "properties": {"project_id": {"type": "string"}, "idempotency_key": {"type": "string"}, "pdv_request_id": {"type": "string"}, "odysseus_agent_run_id": {"type": "string"}, "objective": {"type": "string"}, "timeout_ms": {"type": "integer"}, "dispatch_depth": {"type": "integer", "const": 0}}, "required": ["project_id", "idempotency_key", "pdv_request_id", "odysseus_agent_run_id", "objective", "timeout_ms", "dispatch_depth"], "additionalProperties": False}),
        Tool(name="pdv_dispatch_status", description="Read a correlated Execution OS dispatch state.", inputSchema={"type": "object", "properties": {"dispatch_id": {"type": "string"}}, "required": ["dispatch_id"], "additionalProperties": False}),
        Tool(name="pdv_dispatch_cancel", description="Cancel a correlated Execution OS dispatch when PDV policy authorizes mutation.", inputSchema={"type": "object", "properties": {"dispatch_id": {"type": "string"}, "final_receipt_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["dispatch_id", "final_receipt_id", "reason"], "additionalProperties": False}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    mapped = _TOOL_MAP.get(name)
    if not mapped:
        raise ValueError(f"Unknown PDV tool: {name}")
    payload = await _invoke(mapped, arguments if isinstance(arguments, dict) else {})
    return [TextContent(type="text", text=json.dumps(payload, sort_keys=True))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
