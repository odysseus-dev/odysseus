"""Notes: a note created through the API is readable, editable and gone after delete."""
from __future__ import annotations

NOTES_PATH = "/api/notes"

TITLE = "Odysseus smoke note"
BODY = "Created by the release smoke suite."
EDITED_BODY = "Edited by the release smoke suite."


def test_a_note_round_trips(client):
    created = client.post(NOTES_PATH, json={"title": TITLE, "content": BODY})
    assert created.status_code == 200, created.text
    note_id = created.json()["id"]
    try:
        listed = client.get(NOTES_PATH)
        assert listed.status_code == 200, listed.text
        titles = [n.get("title") for n in listed.json().get("notes") or []]
        assert TITLE in titles, titles

        read = client.get(f"{NOTES_PATH}/{note_id}")
        assert read.status_code == 200, read.text
        assert read.json().get("content") == BODY, read.text

        edited = client.put(f"{NOTES_PATH}/{note_id}",
                            json={"title": TITLE, "content": EDITED_BODY})
        assert edited.status_code == 200, edited.text
        assert client.get(f"{NOTES_PATH}/{note_id}").json()["content"] == EDITED_BODY
    finally:
        removed = client.delete(f"{NOTES_PATH}/{note_id}")
        assert removed.status_code == 200, removed.text
    assert client.get(f"{NOTES_PATH}/{note_id}").status_code == 404
