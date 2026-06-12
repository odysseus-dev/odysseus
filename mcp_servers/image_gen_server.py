"""
image_gen_server.py

MCP server exposing image generation via OpenAI-compatible APIs.
"""

import asyncio
import logging
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

_GENERIC_MCP_ERROR = "Image generation failed unexpectedly. Check local logs for details."

server = Server("image_gen")


def _build_image_content(prompt: str, model: str = "", size: str = "", quality: str = "") -> str:
    """Build the line-oriented payload expected by do_generate_image."""
    safe_prompt = " ".join((prompt or "").splitlines()).strip()
    return "\n".join([safe_prompt, model or "", size or "", quality or ""])


def _mcp_visible_error(err: str) -> str:
    """Return err only when safe for MCP/agent output; otherwise generic."""
    if not err or not isinstance(err, str):
        return _GENERIC_MCP_ERROR
    low = err.lower()
    _LEAK_MARKERS = (
        "://",
        "/users/",
        "\\users\\",
        "/home/",
        "api_key",
        "token",
        "secret",
        "bearer ",
        "sk-",
    )
    if any(m in low for m in _LEAK_MARKERS):
        return _GENERIC_MCP_ERROR
    if err.startswith("Image generation failed (") and "):" in err:
        return _GENERIC_MCP_ERROR
    if err.startswith("Image generation error:"):
        return _GENERIC_MCP_ERROR
    return err


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_image",
            description="Generate an image using an image-capable model (e.g. gpt-image-1)",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Image description prompt"},
                    "model": {"type": "string", "description": "Model name (auto-detects if omitted)"},
                    "size": {"type": "string", "description": "Image size (default 1024x1024)"},
                    "quality": {"type": "string", "description": "Quality: low, medium, high, auto (default medium)"},
                },
                "required": ["prompt"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "generate_image":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    prompt = arguments.get("prompt", "")
    model_spec = arguments.get("model", "")
    size = arguments.get("size", "1024x1024")
    quality = arguments.get("quality", "medium")

    if not prompt:
        return [TextContent(type="text", text="Error: Image prompt is required")]

    try:
        from src.settings import get_setting
        from src.ai_interaction import do_generate_image

        if not get_setting("image_gen_enabled", True):
            return [TextContent(type="text", text="Error: Image generation is disabled by the administrator.")]

        # Delegate to the canonical owner-aware path (OQ-5). This keeps the
        # OpenAI-compatible behavior and adds media-registry / ComfyUI routing
        # in one place rather than duplicating it here.
        #
        # OWNER/SESSION LIMITATION (Gatekeeper F3): this MCP server runs as a
        # separate stdio subprocess and receives no auth/session context, so it
        # cannot attribute generations to an owner. Images created via this path
        # are therefore saved with owner/session unset (acceptable for the
        # single-user local default; revisit before multi-user deployment). The
        # direct chat path (routes/chat_routes.py) DOES pass the real owner and
        # remains owner/session-scoped — do not weaken it.
        content = _build_image_content(prompt, model_spec, size, quality)
        result = await do_generate_image(content, owner=None)

        if isinstance(result, dict) and result.get("error"):
            safe = _mcp_visible_error(result["error"])
            return [TextContent(type="text", text=f"Error: {safe}")]

        image_url = result.get("image_url") if isinstance(result, dict) else None
        if image_url:
            text = (
                f"Generated image for: {prompt[:100]}\n"
                f"image_url: {image_url}\n"
                f"model: {result.get('image_model', model_spec)}\n"
                f"size: {result.get('image_size', size)}"
            )
            return [TextContent(type="text", text=text)]

        # No image_url and no error → degraded-state informational message.
        msg = result.get("results") if isinstance(result, dict) else None
        return [TextContent(type="text", text=msg or "Error: Unexpected image generation response")]

    except Exception:
        logger.exception("MCP generate_image failed")
        return [TextContent(type="text", text=f"Error: {_GENERIC_MCP_ERROR}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
