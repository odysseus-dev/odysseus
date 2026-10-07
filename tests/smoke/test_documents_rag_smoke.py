"""Documents (RAG): an uploaded file is chunked, indexed and then listed.

This is the one area whose dependency is not satisfiable from a clean
checkout. `requirements.txt` pins `chromadb-client`, the HTTP client;
the ChromaDB *server* is a separate install, and without one reachable
the app returns a deliberate 503 from the upload route rather than
indexing into nothing. So the scenario skips with that reason printed in
the table instead of being quietly dropped - a row saying SKIP and why
is the honest report, and it goes green as soon as a vector service is
there.
"""
from __future__ import annotations

import pytest

PERSONAL_PATH = "/api/personal"
UPLOAD_PATH = "/api/personal/upload"

# The route uniquifies the stored name and lists it under the owner's
# upload dir, so assertions match on the stem rather than the filename.
STEM = "odysseus-smoke-corpus"
FILENAME = f"{STEM}.txt"
CONTENT = (
    "The release smoke suite indexed this file. "
    "It exists so the retrieval path has something deterministic to chunk."
)
# The route's own 503 text when no vector store answers.
UNAVAILABLE_MARKER = "RAG system is not available"


def test_an_uploaded_file_is_indexed_and_listed(client):
    response = client.post(UPLOAD_PATH,
                           files={"files": (FILENAME, CONTENT.encode("utf-8"), "text/plain")})
    if response.status_code == 503 and UNAVAILABLE_MARKER in response.text:
        pytest.skip(
            "no vector service reachable, so indexing is unavailable. "
            "requirements.txt pins chromadb-client, not the server; install "
            "chromadb in the venv and re-run to cover this area."
        )
    assert response.status_code == 200, response.text
    body = response.json()
    try:
        assert body.get("indexed_count", 0) > 0, f"nothing was indexed: {body}"
        assert body.get("failed_count", 1) == 0, f"a chunk failed to index: {body}"
        assert FILENAME in (body.get("uploaded") or []), body

        listed = client.get(PERSONAL_PATH)
        assert listed.status_code == 200, listed.text
        names = [str(f.get("name")) for f in listed.json().get("files") or []]
        assert any(STEM in name for name in names), names
    finally:
        listed = client.get(PERSONAL_PATH).json().get("files") or []
        for entry in listed:
            if STEM in str(entry.get("name")):
                client.request("DELETE", "/api/personal/file",
                               params={"filepath": entry.get("path")})
