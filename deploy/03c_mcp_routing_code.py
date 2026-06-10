"""
Phase 3c: MCP Call Routing Code

REPLACE the existing @app.post("/mcp-call") endpoint in odysseus/app.py
(around line 866) with this updated version that routes to actual MCP servers.

This implementation:
- Routes odys-browser-mcp calls to localhost:9090 (Playwright MCP)
- Routes odys-fs-mcp calls to localhost:8100 (PyMCP-FS)
- Handles timeouts and connection errors gracefully
- Logs all MCP calls to F:\odysseus\logs\mcp.log
"""

import logging
import json
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Configure MCP logging (add to app startup)
mcp_logger = logging.getLogger("mcp")
mcp_log_handler = logging.FileHandler("F:\\odysseus\\logs\\mcp.log", encoding="utf-8")
mcp_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))
mcp_logger.addHandler(mcp_log_handler)
mcp_logger.setLevel(logging.INFO)


@app.post("/mcp-call")
async def mcp_call_proxy(request: Request) -> Dict[str, object]:
    """
    Proxy tool calls to MCP servers (Model Context Protocol).

    Phase 3c: Routes to actual MCP servers running on localhost:9090 and 8100.

    Request body:
    {
        "server": "odys-browser-mcp" | "odys-fs-mcp",
        "tool": "tool_name",
        "args": { ...tool-specific arguments... }
    }

    Returns:
    {
        "server": "...",
        "tool": "...",
        "status": "success" | "error",
        "result": {...} | null,
        "error": null | "error message"
    }
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"MCP call: invalid request body: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid request body: {str(e)}"
        )

    server = body.get("server")
    tool = body.get("tool")
    args = body.get("args", {})

    # Validate server name
    valid_servers = {
        "odys-browser-mcp": "http://localhost:9090",
        "odys-fs-mcp": "http://localhost:8100"
    }

    if server not in valid_servers:
        error_msg = f"Unknown MCP server '{server}'. Valid: {list(valid_servers.keys())}"
        logger.warning(f"MCP call: {error_msg}")
        raise HTTPException(
            status_code=400,
            detail=error_msg
        )

    server_url = valid_servers[server]

    # Log the MCP call
    mcp_logger.info(f"MCP call: server={server}, tool={tool}, args_keys={list(args.keys())}")

    try:
        # Import httpx for making HTTP calls to MCP servers
        import httpx

        # Route to appropriate MCP server
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            # MCP servers typically accept tool calls at /v1/tools/call or /tools/call
            # For Playwright MCP: POST {server_url}/v1/tools/call
            # For PyMCP-FS: POST {server_url}/v1/tools/call

            mcp_endpoint = f"{server_url}/v1/tools/call"

            # Prepare MCP request
            mcp_request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool,
                    "arguments": args
                },
                "id": 1
            }

            logger.info(f"MCP: routing {server}:{tool} to {mcp_endpoint}")

            # Forward to MCP server
            response = await client.post(
                mcp_endpoint,
                json=mcp_request,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                result = response.json()
                mcp_logger.info(f"MCP call succeeded: server={server}, tool={tool}")
                return {
                    "server": server,
                    "tool": tool,
                    "status": "success",
                    "result": result,
                    "error": None,
                    "mcp_status": response.status_code
                }
            else:
                error_msg = f"MCP server returned {response.status_code}: {response.text[:200]}"
                mcp_logger.warning(f"MCP call failed: {error_msg}")
                return {
                    "server": server,
                    "tool": tool,
                    "status": "error",
                    "result": None,
                    "error": error_msg,
                    "mcp_status": response.status_code
                }

    except httpx.ConnectError as e:
        error_msg = f"Cannot connect to {server} at {server_url}: {str(e)}"
        mcp_logger.error(f"MCP connection error: {error_msg}")
        return {
            "server": server,
            "tool": tool,
            "status": "error",
            "result": None,
            "error": error_msg,
            "note": "Ensure MCP service is running (Get-Service Odysseus-* | Where Status -eq Running)"
        }

    except httpx.TimeoutException as e:
        error_msg = f"Timeout calling {server} (30s): {str(e)}"
        mcp_logger.error(f"MCP timeout: {error_msg}")
        return {
            "server": server,
            "tool": tool,
            "status": "error",
            "result": None,
            "error": error_msg,
            "note": "MCP server may be overloaded or stuck"
        }

    except Exception as e:
        error_msg = f"Unexpected error calling {server}: {str(e)}"
        mcp_logger.error(f"MCP error: {error_msg}")
        return {
            "server": server,
            "tool": tool,
            "status": "error",
            "result": None,
            "error": error_msg
        }
