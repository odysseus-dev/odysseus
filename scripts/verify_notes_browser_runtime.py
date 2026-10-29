#!/usr/bin/env python3
"""Run real Notes UI and API against a disposable native distribution and database."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stop_owned_server(server, identity):
    """Close only identity-bound descendants, including MCP's new sessions.

    Capture before signalling the parent: an orphaned stderr holder can no
    longer be attributed by ancestry. Never search by executable name or kill
    the caller's process group.
    """
    from src import process_lifecycle as lifecycle

    descendants = (lifecycle.bind_descendants([identity.pid])
                   if identity.verdict() == lifecycle.OWNED else [])
    if os.name == "nt":
        if identity.verdict() == lifecycle.OWNED:
            lifecycle.taskkill_tree(identity.pid)
    else:
        outcome = lifecycle.terminate_identities(
            [*(entry.identity for entry in reversed(descendants)), identity],
            steps=lifecycle.term_kill_steps(10),
        )
        if outcome.survivors or outcome.unverified:
            raise RuntimeError(f"Disposable Notes cleanup could not verify exit: {outcome}")
    try:
        logs, _ = server.communicate(timeout=2)
    except subprocess.TimeoutExpired as error:
        # Do not turn an unidentified pipe holder into an unbounded wait.
        server.stdout.close()
        raise RuntimeError("Disposable Notes output pipe remained open after owned cleanup") from error
    return logs


def main():
    with tempfile.TemporaryDirectory(prefix="ody-notes-native-") as directory, socket.socket() as chroma_guard:
        target = Path(directory)
        for name in ("app.py", "HARNESS_VERSION"):
            shutil.copy2(ROOT / name, target / name)
        for name in ("core", "routes", "src", "services", "resources", "mcp_servers", "companion", "config", "static"):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", ".env", "secrets.env*", "*.db", "*.sqlite*"))
        chroma_guard.bind(("127.0.0.1", 0))
        with socket.socket() as app_port:
            app_port.bind(("127.0.0.1", 0))
            port = app_port.getsockname()[1]
        env = dict(os.environ)
        env.update(
            ODYSSEUS_DATA_DIR=str(target / "data"),
            DATABASE_URL="sqlite:///" + str(target / "notes.db"), AUTH_ENABLED="true",
            ODYSSEUS_STARTUP_WARMUPS="0", ODYSSEUS_TOOL_INDEX_PREWARM="0",
            CHROMADB_HOST="127.0.0.1", CHROMADB_PORT=str(chroma_guard.getsockname()[1]),
            FASTEMBED_CACHE_PATH=str(target / "fastembed"),
        )
        env.pop("PYTHONPATH", None)
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=target, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=(os.name != "nt"),
        )
        from src.process_lifecycle import ProcessIdentity
        server_identity = ProcessIdentity.capture(server.pid)
        result = None
        try:
            origin = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Disposable Notes server exited during startup")
                try:
                    with urlopen(origin + "/", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Disposable Notes server did not become ready")
            result = subprocess.run(
                ["node", "tests/browser/notesNative.mjs"], cwd=ROOT,
                env=dict(os.environ, ODYSSEUS_NOTES_RUNTIME_ORIGIN=origin, NOTES_TEST_PYTHON=sys.executable),
                capture_output=True, text=True, timeout=120,
            )
            print(result.stdout, end="")
            if result.returncode:
                print(result.stderr, file=sys.stderr)
            return result.returncode
        finally:
            logs = _stop_owned_server(server, server_identity)
            if result is None or result.returncode:
                print(logs[-8000:], file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
