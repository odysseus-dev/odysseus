"""Tests for the /todo slash command payload via the notes API."""
from fastapi.testclient import TestClient
from app import app
from core.database import SessionLocal, Note

client = TestClient(app)

def test_todo_slash_command_payload():
    # Simulate the /todo <text> payload that the frontend now sends
    payload = {
        "title": "",
        "items": [{"text": "Buy milk", "done": False}],
        "note_type": "todo",
        "source": "slash"
    }

    response = client.post("/api/notes", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["note_type"] == "todo"
    assert data["title"] == ""
    assert data["source"] == "slash"
    
    # Items should be successfully parsed back into a list of dicts
    assert isinstance(data["items"], list)
    assert len(data["items"]) == 1
    assert data["items"][0]["text"] == "Buy milk"
    assert data["items"][0]["done"] is False

    note_id = data["id"]
    
    # Verify the note is returned in the list endpoint
    list_res = client.get("/api/notes")
    assert list_res.status_code == 200
    list_data = list_res.json()
    
    found = False
    for n in list_data["notes"]:
        if n["id"] == note_id:
            assert n["note_type"] == "todo"
            assert n["items"][0]["text"] == "Buy milk"
            found = True
            break
            
    assert found

    # Clean up
    db = SessionLocal()
    try:
        db.query(Note).filter(Note.id == note_id).delete()
        db.commit()
    finally:
        db.close()
