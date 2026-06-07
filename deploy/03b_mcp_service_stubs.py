"""
Phase 3b: MCP Service Stub Endpoints

Add these endpoints to odysseus/app.py (after the existing health endpoints).
These are placeholder stubs that will be wired to actual MCP servers in Phase 3c.

Usage:
  1. Copy this entire block
  2. Paste after line 826 in app.py (after the runtime_info() endpoint)
  3. No code changes needed to routes/ — stubs are self-contained
"""

# ========= MCP SERVICE STUBS (Phase 3b) =========
# These endpoints provide the API surface for MCP server integration.
# Actual MCP servers (Playwright, PyMCP-FS) will be deployed in Phase 3c.

@app.get("/mcp/status")
async def mcp_status_check() -> Dict[str, object]:
    """
    Health check for MCP servers (Model Context Protocol).

    Returns status of each configured MCP server.
    Phase 3b: Returns "pending" for all servers (not deployed yet).
    Phase 3c: Will check actual server sockets after deployment.
    """
    return {
        "mcp_servers": {
            "odys-browser-mcp": {
                "status": "pending",
                "description": "Browser automation via Playwright MCP",
                "version": "0.1.0",
                "deployment_target": "systemd service",
                "expected_port": None,  # Will be Unix socket or stdio
                "notes": "Awaiting Phase 3c deployment"
            },
            "odys-fs-mcp": {
                "status": "pending",
                "description": "Filesystem operations (read/write/list/search)",
                "version": "0.1.0",
                "deployment_target": "systemd service",
                "expected_port": None,
                "notes": "Awaiting Phase 3c deployment"
            }
        },
        "overall_status": "pending",
        "phase": "3b (stubs)",
        "notes": "MCP servers will be deployed as systemd services in Phase 3c"
    }


@app.post("/mcp-call")
async def mcp_call_proxy(request: Request) -> Dict[str, object]:
    """
    Proxy tool calls to MCP servers (Model Context Protocol).

    Request body:
    {
        "server": "odys-browser-mcp" | "odys-fs-mcp",
        "tool": "open_page" | "take_screenshot" | "fill_form" | ...,
        "args": { ...tool-specific arguments... }
    }

    Phase 3b: Returns "not ready" error (servers not deployed yet).
    Phase 3c: Will forward to actual MCP server sockets.
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid request body: {str(e)}"
        )

    server = body.get("server")
    tool = body.get("tool")
    args = body.get("args", {})

    # Validate server name
    valid_servers = ["odys-browser-mcp", "odys-fs-mcp"]
    if server not in valid_servers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown MCP server '{server}'. Valid: {valid_servers}"
        )

    # Phase 3b stub response
    return {
        "server": server,
        "tool": tool,
        "status": "error",
        "error": "MCP servers not deployed yet (Phase 3b stub)",
        "message": f"Server '{server}' will be available after Phase 3c deployment",
        "phase": "3b",
        "notes": {
            "odys-browser-mcp": "Deploy as: systemd service (Playwright MCP binary)",
            "odys-fs-mcp": "Deploy as: systemd service (PyMCP-FS Python module)"
        }
    }


# ========= END MCP SERVICE STUBS =========
# These stubs provide the contract for Phase 3c integration.
# Actual MCP server deployment will:
#   1. Start systemd services for each MCP server
#   2. Update status checks to connect to actual server sockets
#   3. Forward /mcp-call requests to the appropriate server
#   4. Handle authentication + scoping (via token)
