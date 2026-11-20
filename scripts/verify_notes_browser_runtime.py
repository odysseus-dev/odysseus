#!/usr/bin/env python3
"""Run real Notes UI and API against a disposable native distribution and database.

`--browser webkit` runs the same flows in Playwright's WebKit instead of
Chromium. The instance gets a stub OpenAI-compatible model
(`tests/browser/openai_stub.py`) as its only endpoint, so agent runs are
deterministic and the tools they were offered can be read back.
"""
import argparse
import json
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
SEED_LEGACY_NOTE = """
import json
from core.database import Base, SessionLocal, engine, Note
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    db.add(Note(id="legacy-checklist", owner="notes-reference", title="Legacy checklist", note_type="todo",
                items=json.dumps([{"text": "Seeded first", "done": False}, {"text": "Seeded second", "done": True}])))
    # A legacy goal (items JSON) and a drawing (image_url column + gallery
    # image), the two stored note types that must survive an edit.
    db.add(Note(id="legacy-goal", owner="notes-reference", title="Legacy goal", note_type="goal",
                items=json.dumps([{"text": "Goal step one", "done": True}, {"text": "Goal step two", "done": False}])))
    db.add(Note(id="legacy-drawing", owner="notes-reference", title="Legacy drawing", note_type="draw",
                image_url="/api/upload/0123456789abcdef0123456789abcdef.png", gallery_id="gallery-legacy-drawing"))
    db.commit()
"""


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_http(url, process, label, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited during startup")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"{label} did not become ready")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="ody-notes-native-") as directory, socket.socket() as chroma_guard:
        target = Path(directory)
        for name in ("app.py", "HARNESS_VERSION"):
            shutil.copy2(ROOT / name, target / name)
        for name in ("core", "routes", "src", "services", "resources", "mcp_servers", "companion", "config", "static"):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", ".env", "secrets.env*", "*.db", "*.sqlite*"))
        chroma_guard.bind(("127.0.0.1", 0))
        port = _free_port()
        env = dict(os.environ)
        env.update(
            ODYSSEUS_DATA_DIR=str(target / "data"),
            DATABASE_URL="sqlite:///" + str(target / "notes.db"), AUTH_ENABLED="true",
            ODYSSEUS_STARTUP_WARMUPS="0", ODYSSEUS_TOOL_INDEX_PREWARM="0",
            CHROMADB_HOST="127.0.0.1", CHROMADB_PORT=str(chroma_guard.getsockname()[1]),
            FASTEMBED_CACHE_PATH=str(target / "fastembed"),
        )
        env.pop("PYTHONPATH", None)
        # A note in the pre-markdown shape (items JSON + image_url columns), so
        # the real startup migration folds it before the browser looks at it.
        subprocess.run([sys.executable, "-c", SEED_LEGACY_NOTE], cwd=target, env=env, check=True)
        # The server logs to a file: an undrained pipe fills after ~64 KB of
        # access log, the server blocks on its next write and requests hang.
        stub_port = _free_port()
        stub_log = target / "model-requests.jsonl"
        stub = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "browser" / "openai_stub.py"), str(stub_port), str(stub_log)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log_path = target / "server.log"
        log_file = open(log_path, "w")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=target, env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True,
            start_new_session=(os.name != "nt"),
        )
        from src.process_lifecycle import ProcessIdentity
        server_identity = ProcessIdentity.capture(server.pid)
        result = None
        try:
            origin = f"http://127.0.0.1:{port}"
            stub_origin = f"http://127.0.0.1:{stub_port}"
            _wait_http(stub_origin + "/v1/models", stub, "Stub model")
            # A slow disk can take longer than the default to boot the app.
            _wait_http(origin + "/", server, "Disposable Notes server", float(os.environ.get("NOTES_SERVER_START_TIMEOUT", 45)))
            result = subprocess.run(
                ["node", "tests/browser/notesNative.mjs"], cwd=ROOT,
                env=dict(
                    os.environ, ODYSSEUS_NOTES_RUNTIME_ORIGIN=origin, NOTES_TEST_PYTHON=sys.executable,
                    NOTES_BROWSER=args.browser, NOTES_STUB_ORIGIN=stub_origin, NOTES_STUB_LOG=str(stub_log),
                    NOTES_DB_PATH=str(target / "notes.db"),
                ),
                capture_output=True, text=True, timeout=float(os.environ.get("NOTES_RUN_TIMEOUT", 900)),
            )
            print(result.stdout, end="")
            if result.returncode:
                print(result.stderr, file=sys.stderr)
            return result.returncode
        finally:
            stub.terminate()
            stub.wait(timeout=10)
            _stop_owned_server(server, server_identity)
            log_file.close()
            # NOTES_SERVER_LOG=<path> keeps the whole server log (the temp dir goes).
            if os.environ.get("NOTES_SERVER_LOG"):
                shutil.copyfile(log_path, os.environ["NOTES_SERVER_LOG"])
            if result is None or result.returncode:
                print(log_path.read_text(encoding="utf-8", errors="replace")[-8000:], file=sys.stderr)
                if stub_log.exists():
                    print("--- model requests: offered tools, last message ---", file=sys.stderr)
                    for line in stub_log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]:
                        body = json.loads(line)["body"]
                        tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
                        last = (body.get("messages") or [{}])[-1]
                        print(tools, last.get("role"), repr(str(last.get("content"))[:300]), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
