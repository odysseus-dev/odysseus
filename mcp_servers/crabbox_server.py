"""
crabbox_server.py

MCP server that lets the Odysseus agent run code on a fresh, throwaway cloud
sandbox instead of on the Odysseus host.

This is the *reverse* of the repo's `crabbox.sh`: there, a human uses crabbox to
run Odysseus on a disposable box. Here, Odysseus's agent uses crabbox to run its
own (possibly AI-generated) commands on a disposable box — so untrusted code
never touches the machine Odysseus runs on. islo.dev's whole reason for being is
"a secure sandbox for coding agents"; this wires that in.

It shells out to the same `crabbox` binary that `crabbox.sh` installs
(https://crabbox.sh). Optional, and degrades cleanly: if `crabbox` isn't
installed or no islo.dev key is present, the tool returns a helpful message
instead of failing the server.
"""

import asyncio
import os
import shutil

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("crabbox")

MAX_OUTPUT_CHARS = 12_000
DEFAULT_IMAGE = os.environ.get(
    "CRABBOX_MCP_IMAGE", "docker.io/library/python:3.12-slim"
)
DEFAULT_PROVIDER = os.environ.get("CRABBOX_MCP_PROVIDER", "islo")
DEFAULT_TIMEOUT = int(os.environ.get("CRABBOX_MCP_TIMEOUT", "300"))


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = "" if text is None else str(text)
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text


def _find_crabbox() -> str | None:
    """Locate the crabbox binary, including common install dirs not on PATH."""
    found = shutil.which("crabbox")
    if found:
        return found
    for cand in (
        os.path.expanduser("~/.local/bin/crabbox"),
        "/opt/homebrew/bin/crabbox",
        "/usr/local/bin/crabbox",
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _have_islo_key() -> bool:
    return bool(
        os.environ.get("ISLO_API_KEY") or os.environ.get("CRABBOX_ISLO_API_KEY")
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="crabbox_run",
            description=(
                "Run a shell command on a FRESH, THROWAWAY cloud sandbox (via crabbox + "
                "islo.dev) and return its combined output and exit code. Use this to "
                "execute untrusted, experimental, or AI-generated code safely — it runs "
                "on a disposable microVM, never on the Odysseus host, and the box is "
                "released when the command finishes. The sandbox starts from a clean "
                "python:3.12-slim image with internet access; install anything you need "
                "as part of the command (e.g. 'pip install X && python script.py'). "
                "Cold start is ~30-90s."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run on the sandbox (runs under bash -lc).",
                    },
                    "image": {
                        "type": "string",
                        "description": f"Container image for the box. Default: {DEFAULT_IMAGE}",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": f"Max seconds to wait. Default: {DEFAULT_TIMEOUT}",
                    },
                },
                "required": ["command"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "crabbox_run":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    command = (arguments or {}).get("command", "").strip()
    if not command:
        return [TextContent(type="text", text="Error: 'command' is required.")]

    crabbox = _find_crabbox()
    if not crabbox:
        return [TextContent(type="text", text=(
            "crabbox is not installed. Install it (see https://crabbox.sh):\n"
            "  brew install openclaw/tap/crabbox\n"
            "or run this repo's ./crabbox.sh once, which auto-installs it."
        ))]

    provider = DEFAULT_PROVIDER
    image = (arguments.get("image") or DEFAULT_IMAGE).strip()
    timeout = int(arguments.get("timeout_seconds") or DEFAULT_TIMEOUT)

    if provider == "islo" and not _have_islo_key():
        return [TextContent(type="text", text=(
            "No islo.dev key found. Set ISLO_API_KEY (or CRABBOX_ISLO_API_KEY) in "
            "Odysseus's environment (.env). Mint one with: islo api-key create <name>."
        ))]

    # --no-sync: the agent's box is a CLEAN sandbox. We deliberately do NOT rsync
    # the Odysseus host's working tree into it — the command is self-contained
    # (clone/install what it needs). This also means crabbox doesn't require the
    # host process to be running inside a git repo.
    argv = [
        crabbox, "run",
        "--provider", provider,
        "--islo-image", image,
        "--no-sync",
        "--", "bash", "-lc", command,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return [TextContent(type="text", text=(
                f"crabbox_run timed out after {timeout}s. The box is released on the "
                "crabbox side; raise timeout_seconds for slower workloads."
            ))]
        text = out.decode("utf-8", "replace") if out else ""
        footer = f"\n[crabbox: provider={provider} image={image} exit={proc.returncode}]"
        return [TextContent(type="text", text=_truncate(text) + footer)]
    except Exception as e:  # never crash the server on a bad run
        return [TextContent(type="text", text=f"crabbox_run failed: {type(e).__name__}: {e}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
