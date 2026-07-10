"""Direct launcher for the built-in Email MCP package."""

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp_servers.email_server import run


if __name__ == "__main__":
    asyncio.run(run())
