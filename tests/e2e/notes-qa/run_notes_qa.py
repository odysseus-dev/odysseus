#!/usr/bin/env python3
"""Boot a disposable Odysseus and run the Notes regression specs against it.

The instance gets its own data dir and SQLite file, ChromaDB on a dead port,
AUTH_ENABLED=true, an admin and one non-admin account, and the repo's stub
model (`tests/browser/openai_stub.py`) as the admin's only endpoint. Both
specs' environment variables are filled in from those accounts, and every
extra argument goes to `npx playwright test`, e.g.:

    venv/bin/python tests/e2e/notes-qa/run_notes_qa.py                 # both specs, Chromium
    venv/bin/python tests/e2e/notes-qa/run_notes_qa.py -g BLOCKER
    NOTES_QA_BROWSERS=chromium,webkit venv/bin/python tests/e2e/notes-qa/run_notes_qa.py notes-edge-cases

`--port` defaults to 7861. Port 7860 is refused. `--keep` leaves the instance
running after the specs finish and prints how to stop it.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONFIG = HERE / "playwright.config.js"


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_http(url, process, label, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited during startup")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"{label} did not become ready")


class Api:
    """A tiny cookie-keeping JSON client, so setup needs nothing beyond the stdlib."""

    def __init__(self, origin):
        self.origin = origin
        self.cookie = ""

    def call(self, method, path, body=None, form=None):
        headers = {"Cookie": self.cookie} if self.cookie else {}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif form is not None:
            boundary = secrets.token_hex(12)
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in form.items()]
            data = ("".join(parts) + f"--{boundary}--\r\n").encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        with urlopen(Request(self.origin + path, data=data, method=method, headers=headers), timeout=30) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
            if cookies:
                self.cookie = "; ".join(c.split(";", 1)[0] for c in cookies)
            return json.loads(response.read() or b"null")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--keep", action="store_true", help="leave the instance up after the run")
    args, playwright_args = parser.parse_known_args(argv)
    if args.port == 7860:
        parser.error("7860 is the live instance; pick another port")

    target = Path(tempfile.mkdtemp(prefix="odysseus-notes-qa-"))
    chroma_guard = socket.socket()
    chroma_guard.bind(("127.0.0.1", 0))  # bound, never listening: Chroma calls fail fast
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(
        ODYSSEUS_DATA_DIR=str(target / "data"), DATABASE_URL="sqlite:///" + str(target / "notes.db"),
        AUTH_ENABLED="true", ODYSSEUS_STARTUP_WARMUPS="0", ODYSSEUS_TOOL_INDEX_PREWARM="0",
        CHROMADB_HOST="127.0.0.1", CHROMADB_PORT=str(chroma_guard.getsockname()[1]),
        FASTEMBED_CACHE_PATH=str(target / "fastembed"),
    )
    stub_port = _free_port()
    stub = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "browser" / "openai_stub.py"), str(stub_port), str(target / "model-requests.jsonl")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    log_path = target / "server.log"
    log_file = open(log_path, "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=ROOT, env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True,
    )
    origin = f"http://127.0.0.1:{args.port}"
    returncode = 1
    try:
        _wait_http(f"http://127.0.0.1:{stub_port}/v1/models", stub, "Stub model", 30)
        _wait_http(origin + "/", server, "Scratch server", float(os.environ.get("NOTES_SERVER_START_TIMEOUT", 90)))
        # NOTES_QA_PASSWORD sets both accounts' password, to log into a --keep
        # instance afterwards. Otherwise they are random and never written down.
        chosen = os.environ.get("NOTES_QA_PASSWORD")
        owner = {"username": "alice", "password": chosen or secrets.token_urlsafe(18) + "Aa1!"}
        collab = {"username": "bob", "password": chosen or secrets.token_urlsafe(18) + "Aa1!"}
        admin = Api(origin)
        admin.call("POST", "/api/auth/setup", owner)
        admin.call("POST", "/api/auth/login", owner)
        admin.call("POST", "/api/auth/users", {**collab, "is_admin": False})
        admin.call("POST", "/api/model-endpoints", form={
            "name": "Stub", "base_url": f"http://127.0.0.1:{stub_port}/v1", "pinned_models": "stub-model", "supports_tools": "true",
        })
        spec_env = dict(
            os.environ,
            NOTES_QA_ORIGIN=origin, NOTES_QA_OWNER=owner["username"], NOTES_QA_OWNER_PW=owner["password"],
            NOTES_QA_COLLAB=collab["username"], NOTES_QA_COLLAB_PW=collab["password"],
            NOTES_ORIGIN=origin, NOTES_USER=owner["username"], NOTES_PASS=owner["password"],
            NOTES_USER2=collab["username"], NOTES_PASS2=collab["password"],
        )
        returncode = subprocess.run(["npx", "playwright", "test", "-c", str(CONFIG), *playwright_args], cwd=ROOT, env=spec_env).returncode
        if args.keep:
            login = "NOTES_QA_PASSWORD" if chosen else "random passwords (set NOTES_QA_PASSWORD to log in to a kept instance)"
            print(f"\nKept: {origin} (server PID {server.pid}, stub PID {stub.pid}), data in {target}, accounts alice and bob, {login}")
            print(f"Stop with: kill {server.pid} {stub.pid}")
            return returncode
    finally:
        if not args.keep:
            for process in (stub, server):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            log_file.close()
            print(f"Server log: {log_path}")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
