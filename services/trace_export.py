import json
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when executing this file directly.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# SessionLocal is imported ONLY for temporary testing.
from typing import List
from core.database import Session as DbSession, ChatMessage as DbChatMessage, SessionLocal

def export_json(file_name: str, data: list[dict]):
    with open(f'{file_name}.json', 'w') as json_file:
        json.dump(data, json_file, indent=4, default=str)
    print(f"Data written to '{file_name}.json'")


# current_user will be passed from the router
# In services/trace_export.py:

def build_trace_records(
    db,
    current_user: str,
    message_ids: List[str],
    session_id: str,
    label: str,
    note: str="",
):
    """Export selected chat messages for `session_id` using the provided
    SQLAlchemy `db` session.
    """
    if not current_user:
        print("Error: No authenticated user found")
        return

    print(f"Exporting data for user: {current_user}\n")

    # Verify session ownership
    session_row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == current_user).first()
    
    if not session_row:
        print("Error: Session not found or unauthorized")
        return
    if session_row.owner != current_user:
        print("Error: Session not found or unauthorized")
        return

    print(f"Processing session '{session_id}'\n")

    if not message_ids:
        print("Error: No message_ids provided")
        return

    messages = (
        db.query(DbChatMessage)
        .filter(DbChatMessage.session_id == session_id,
                DbChatMessage.id.in_(message_ids))
        .order_by(DbChatMessage.timestamp)
        .all()
    )

    messages_list = []
    for msg in messages:
        meta_val = None
        if hasattr(msg, "metadata"):
            meta_val = getattr(msg, "metadata")
        elif hasattr(msg, "meta_data"):
            meta_val = getattr(msg, "meta_data")

        metadata = None
        if isinstance(meta_val, str):
            try:
                metadata = json.loads(meta_val)
            except Exception:
                metadata = {}
        elif isinstance(meta_val, dict):
            metadata = meta_val

        message_data = {
            "id": getattr(msg, "id", None),
            "role": getattr(msg, "role", None),
            "content": getattr(msg, "content", None),
            "timestamp": getattr(msg, "timestamp", None),
        }

        if metadata is not None:
            message_data["metadata"] = metadata

        messages_list.append(message_data)

    session_properties = {
        "name": session_row.name,
        "id": session_row.id,
        "owner": session_row.owner,
        "model": session_row.model,
        "message_count": session_row.message_count,
        "created_at": session_row.created_at,
        "label": label,
        "messages": messages_list,
        "note": note
    }

    export_json("chats_export", [session_properties])
    print(f"✓ Exported trace for session '{session_id}' user '{current_user}'")
    
    # FIXED: Return the data so the router has something to send to the frontend!
    return session_properties

# Tests:
def inspect_messages(session_id: str):
    db = SessionLocal()

    try:
        messages = (
            db.query(DbChatMessage)
            .filter(DbChatMessage.session_id == session_id)
            .all()
        )

        print(f"Found {len(messages)} messages:\n")

        for m in messages[:10]:
            print({
                "id": m.id,
                "role": m.role,
                "content": (
                    m.content[:50]
                    if m.content else None
                )
            })

    finally:
        db.close()

def inspect_sessions():
    db = SessionLocal()

    try:
        sessions = db.query(DbSession).all()

        print(f"Found {len(sessions)} sessions:\n")

        for s in sessions[:10]:
            print({
                "id": s.id,
                "name": s.name,
                "owner": s.owner,
                "model": s.model,
            })

    finally:
        db.close()


def test_build_trace_records():
    db = SessionLocal()
    try:
        build_trace_records(
            db=db,
            current_user="admin",
            session_id="8571ae67-b6f8-471c-bba7-6ce26e786d8d",
            message_ids=[
                "6b19fa1a-df3c-49cf-95b2-6c7ffe37c975",
                "adcadbf1-d197-491d-b7b6-63fba7c273e6",
            ],
            label="success",
            note="needs review",
        )

        print("Test completed successfully")
    finally:
        db.close()

if __name__ == "__main__":
    # inspect_sessions()
    # inspect_messages("8571ae67-b6f8-471c-bba7-6ce26e786d8d")
    test_build_trace_records()
