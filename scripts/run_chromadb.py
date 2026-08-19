"""
Local ChromaDB HTTP server launcher for Odysseus (no Docker needed).

Odysseus connects to Chroma via chromadb.HttpClient(host, port) on
localhost:8100 (see src/chroma_client.py). When running natively on Windows
without Docker, that service is absent and Memory/RAG vector features stay
DEGRADED. This script stands up the bundled chromadb server with a persistent
on-disk store so those features come back.

Run:
    ./venv/Scripts/python.exe scripts/run_chromadb.py
Then restart Odysseus (or it will reconnect lazily).
"""

import os
import sys

# NOTE: The PyPI `chromadb` wheel ships as a "thin" (HTTP-only) client with
# `is_thin_client = True` hardcoded in
#   venv/Lib/site-packages/chromadb/is_thin_client.py
# The thin build cannot run a local storage backend, so a self-hosted Chroma
# server would boot but hang on every storage call. We installed the
# `chromadb[server]` extra (which brings chromadb_rust_bindings); with those
# bindings present the local engine works once that flag is flipped to False.
# Flip it on disk (it is not a mutable module attribute — chromadb rebinds the
# submodule name to the bool at import). If chromadb is reinstalled, re-apply:
#   echo 'is_thin_client = False' > venv/Lib/site-packages/chromadb/is_thin_client.py
from chromadb.config import Settings
from chromadb.server.fastapi import FastAPI as ChromaFastAPI

# Keep the persistent store in the project data dir so it survives restarts.
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PERSIST = os.path.join(HERE, "..", "data", "chroma")

HOST = os.getenv("CHROMADB_HOST", "127.0.0.1")
PORT = int(os.getenv("CHROMADB_PORT", "8100"))
PERSIST = os.getenv("CHROMADB_PERSIST_DIR", DEFAULT_PERSIST)


def main() -> int:
    os.makedirs(PERSIST, exist_ok=True)

    settings = Settings(
        is_persistent=True,
        persist_directory=PERSIST,
        allow_reset=True,
        chroma_server_host=HOST,
        chroma_server_http_port=PORT,
        # Keep telemetry off so nothing reaches the network (Posthog capture
        # is a no-op and OTel granularity defaults to "none" anyway).
        anonymized_telemetry=False,
    )

    server = ChromaFastAPI(settings)
    asgi = server.app()

    import uvicorn

    print(f"[run_chromadb] serving Chroma on http://{HOST}:{PORT} (persist={PERSIST})")
    uvicorn.run(asgi, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
