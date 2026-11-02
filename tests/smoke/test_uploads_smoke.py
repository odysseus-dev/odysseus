"""Uploads: a file uploaded through the chat attachment route reads back byte for byte."""
from __future__ import annotations

UPLOAD_PATH = "/api/upload"
STATS_PATH = "/api/upload/stats"

FILENAME = "odysseus-smoke-attachment.txt"
CONTENT = b"Uploaded by the release smoke suite."


def test_an_upload_reads_back_unchanged(client):
    response = client.post(UPLOAD_PATH,
                           files={"files": (FILENAME, CONTENT, "text/plain")})
    assert response.status_code == 200, response.text
    files = response.json().get("files") or []
    assert len(files) == 1, response.text
    entry = files[0]
    assert entry.get("name") == FILENAME, entry
    assert entry.get("size") == len(CONTENT), entry

    fetched = client.get(f"{UPLOAD_PATH}/{entry['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == CONTENT, fetched.content

    stats = client.get(STATS_PATH)
    assert stats.status_code == 200, stats.text
    assert stats.json().get("total_files", 0) >= 1, stats.text
