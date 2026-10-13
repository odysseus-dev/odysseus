#!/usr/bin/env python3
"""Verify the current Python runtime from an isolated distribution without frontend tooling."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
from pathlib import Path
import shutil
from starlette.testclient import TestClient
from app import app
assert not Path('frontend').exists()
assert not Path('node_modules').exists()
assert shutil.which('node') is None
with TestClient(app) as client:
    response = client.get('/static/js/actionMenuOrder.js')
    assert response.status_code == 200, response.status_code
    assert response.content == Path('static/js/actionMenuOrder.js').read_bytes()
    assert response.headers['content-type'].startswith('text/javascript'), response.headers
    assert response.headers['cache-control'] == 'no-cache', response.headers
    assert "script-src 'self' 'nonce-" in response.headers['content-security-policy'], response.headers
    assert client.get('/frontend/src/shared/actionMenuOrder.js').status_code == 404
    assert client.get('/static/%2e%2e/app.py').status_code == 404
    shell = client.get('/')
    assert shell.status_code == 200, shell.status_code
    assert '{{CSP_NONCE}}' not in shell.text
    assert '/static/app.js' in shell.text
print('PASS: isolated Python lifespan, shell, deployed bytes, MIME, revalidation, CSP and source-tree 404')
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ody-f1-distribution-") as directory, socket.socket() as chroma_guard:
        target = Path(directory)
        for name in ("app.py", "HARNESS_VERSION"):
            shutil.copy2(ROOT / name, target / name)
        for name in ("core", "routes", "src", "services", "resources", "mcp_servers", "companion", "config", "static", "scripts"):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "node_modules", ".env", "secrets.env*", "*.db", "*.sqlite*"))
        # Reserve a non-listening port so initialization cannot reach a real store.
        chroma_guard.bind(("127.0.0.1", 0))
        env = dict(os.environ)
        env.update(
            ODYSSEUS_DATA_DIR=str(target / "data"),
            DATABASE_URL="sqlite:///:memory:",
            AUTH_ENABLED="false",
            ODYSSEUS_STARTUP_WARMUPS="0",
            ODYSSEUS_TOOL_INDEX_PREWARM="0",
            CHROMADB_HOST="127.0.0.1",
            CHROMADB_PORT=str(chroma_guard.getsockname()[1]),
            FASTEMBED_CACHE_PATH=str(target / "fastembed"),
            PATH=str(target / "no-development-tools"),
        )
        # A caller's Python search path must not import the original checkout.
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-c", PROBE], cwd=target, env=env, capture_output=True, text=True, timeout=60)
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr[-8000:], file=sys.stderr)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
