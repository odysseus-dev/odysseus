"""Documents: the editor's create, edit and version history survive a round trip."""
from __future__ import annotations

DOCUMENT_PATH = "/api/document"
LIBRARY_PATH = "/api/documents/library"

TITLE = "Odysseus smoke document"
FIRST = "First revision, written by the release smoke suite."
SECOND = "Second revision, written by the release smoke suite."


def test_a_document_round_trips_with_its_versions(client):
    created = client.post(DOCUMENT_PATH, json={"title": TITLE, "content": FIRST})
    assert created.status_code == 200, created.text
    body = created.json()
    doc_id = body["id"]
    try:
        assert body.get("current_content") == FIRST, body
        assert body.get("version_count") == 1, body

        library = client.get(LIBRARY_PATH)
        assert library.status_code == 200, library.text
        assert doc_id in [d.get("id") for d in library.json().get("documents") or []]

        # `force_version` because a save inside the route's coalesce
        # window updates the current version in place instead of adding
        # one - which is right for autosave and would make a smoke check
        # that edits immediately depend on the clock.
        edited = client.put(f"{DOCUMENT_PATH}/{doc_id}",
                            json={"content": SECOND, "force_version": True})
        assert edited.status_code == 200, edited.text
        assert edited.json().get("current_content") == SECOND, edited.text
        assert edited.json().get("version_count") == 2, edited.text

        versions = client.get(f"{DOCUMENT_PATH}/{doc_id}/versions")
        assert versions.status_code == 200, versions.text
        contents = {v.get("version_number"): v.get("content") for v in versions.json()}
        assert contents.get(1) == FIRST, contents
        assert contents.get(2) == SECOND, contents

        restored = client.post(f"{DOCUMENT_PATH}/{doc_id}/restore/1")
        assert restored.status_code == 200, restored.text
        assert client.get(f"{DOCUMENT_PATH}/{doc_id}").json()["current_content"] == FIRST
    finally:
        removed = client.delete(f"{DOCUMENT_PATH}/{doc_id}")
        assert removed.status_code == 200, removed.text
