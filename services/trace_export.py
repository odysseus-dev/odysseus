import json
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when executing this file directly.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from typing import List
from core.database import Session as DbSession, ChatMessage as DbChatMessage

def redact_sensitive_data(session_properties: dict) -> dict:
    """Scrub known API keys from message content and metadata."""
    
    try:
        with open("data/settings.json", "r") as f:
            settings = json.load(f)
    except FileNotFoundError:
        print("Warning: /data/settings.json not found. Skipping redaction.")
        return session_properties

    raw_secrets = [
        settings.get("brave_api_key"),
        settings.get("google_pse_key"),
        settings.get("google_pse_cx"),
        settings.get("tavily_api_key"),
        settings.get("serper_api_key")
    ]
    
    secrets_to_hide = [s for s in raw_secrets if s and isinstance(s, str)]

    if not secrets_to_hide:
        return session_properties

    def scrub_text(text: str) -> str:
        if not text:
            return text
        for secret in secrets_to_hide:
            text = text.replace(secret, "[REDACTED]")
        return text

    for msg in session_properties.get("messages", []):
        
        if isinstance(msg.get("content"), str):
            msg["content"] = scrub_text(msg["content"])
            
        if msg.get("metadata"):
            meta_str = json.dumps(msg["metadata"])
            scrubbed_meta_str = scrub_text(meta_str)
            msg["metadata"] = json.loads(scrubbed_meta_str)

    return session_properties

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
    if not message_ids:
        raise ValueError("Export failed: Message ID list cannot be empty.")
    
    if not current_user:
        print("Error: No authenticated user found")
        return

    print(f"Exporting data for user: {current_user}\n")

    # Verify session ownership
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == current_user).first()
    
    if not session:
       raise KeyError("Export failed: Session not found or unauthorized.")
        
    if session.owner != current_user:
        raise PermissionError("Export failed: You do not have permission to access this session.")
        

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

    if len(messages) != len(message_ids):
        raise ValueError("Mismatch: Some requested messages do not belong to this session")

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
        "name": session.name,
        "id": session.id,
        "owner": session.owner,
        "model": session.model,
        "message_count": session.message_count,
        "created_at": session.created_at,
        "label": label,
        "messages": messages_list,
        "note": note
    }

    print(f"✓ Exported trace for session '{session_id}' user '{current_user}'")
    
    return redact_sensitive_data(session_properties) 
