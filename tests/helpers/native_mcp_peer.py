"""Tiny real MCP stdio peer: no model, credentials, network or GPU."""
import asyncio
import os
from pathlib import Path
import sys
import time

from mcp.server.fastmcp import FastMCP

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
(root / 'peer.pid').write_text(str(os.getpid()))
server = FastMCP('quiescence-fixture')


@server.tool()
async def pause() -> str:
    (root / 'entered').touch()
    deadline = time.monotonic() + 20
    while not (root / 'release').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('Fixture release was not supplied')
        await asyncio.sleep(.01)
    return 'fixture-completed'


if __name__ == '__main__':
    server.run(transport='stdio')
    deadline = time.monotonic() + 30
    while (root / 'hold-exit').exists() and time.monotonic() < deadline:
        time.sleep(.01)
