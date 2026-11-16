"""Lab ASGI app and real persistent HTTP sidecars; never an inference runtime."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKERS = []
MCP: Any = None
MCP_OWNER = None


def port_live(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) == 0


async def app(scope, receive, send):
    global MCP, MCP_OWNER
    if scope['type'] == 'lifespan':
        while True:
            event = await receive()
            if event['type'] == 'lifespan.startup':
                from src.mcp_manager import McpManager
                MCP = McpManager()
                port = int(os.environ['ODYSSEUS_RESTART_LAB_GATEWAY_PORT'])
                if not port_live(port):
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--port', str(port)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                for _ in range(100):
                    if port_live(port):
                        break
                    await asyncio.sleep(.02)
                await send({'type': 'lifespan.startup.complete'})
            elif event['type'] == 'lifespan.shutdown':
                hold = Path(os.environ['ODYSSEUS_RESTART_LAB_DIR']) / 'shutdown-hold'
                while hold.exists():
                    await asyncio.sleep(.05)
                # Gateway child deliberately survives the app's exit.
                await send({'type': 'lifespan.shutdown.complete'})
                return
    elif scope['type'] == 'http':
        body = None
        if scope['path'] == '/fixture-mcp-start':
            ready = asyncio.Event()
            peer = Path(__file__).with_name('native_mcp_peer.py')
            directory = Path(os.environ['ODYSSEUS_RESTART_LAB_DIR']) / 'mcp-peer'
            async def owner():
                connected = await MCP.connect_server('fixture', 'fixture', 'stdio',
                    command=sys.executable, args=[str(peer), str(directory)])
                ready.set()
                if connected:
                    await MCP.hold_owned_connection('fixture')
            MCP_OWNER = asyncio.create_task(owner())
            await asyncio.wait_for(ready.wait(), 10)
            body = {'connected': 'fixture' in MCP._sessions}
        elif scope['path'] == '/fixture-mcp-call':
            body = await MCP.call_tool('mcp__fixture__pause', {})
        if scope['path'] == '/fixture-spawn-worker':
            WORKERS.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW))
        elif scope['path'] == '/fixture-clear-worker':
            for worker in WORKERS:
                if worker.poll() is None:
                    worker.terminate()
                worker.wait(timeout=3)
            WORKERS.clear()
        await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
        if body is None:
            body = {'status': 'healthy'} if scope['path'] == '/api/health' else {'pid': os.getpid()}
        await send({'type': 'http.response.body', 'body': json.dumps(body).encode()})
        if scope['path'] == '/fixture-exit':
            asyncio.get_running_loop().call_later(.1, os._exit, 0)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    if args.port in {7000, 9130, 8100}:
        raise SystemExit('Fixture refuses production ports')
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            payload = ({'nanosecond heartbeat': time.time_ns()} if self.path == '/api/v2/heartbeat'
                       else {'ok': True, 'auth_required': False, 'displayVersion': 'isolated-fixture'})
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            self.do_GET()
            if self.path == '/fixture-stop':
                threading.Thread(target=self.server.shutdown, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.serve_forever()
    server.server_close()
