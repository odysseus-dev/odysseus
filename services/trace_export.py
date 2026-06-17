import json
import re
from typing import List
from core.database import Session as DbSession, ChatMessage as DbChatMessage

GENERIC_SENSITIVE_PATTERNS = [
    # Auth Headers & Bearer Tokens (catches common token prefixes)
    re.compile(r'(?i)(bearer|token|auth|authorization|x-api-key)\s*[:=\s]\s*["\']?[a-zA-Z0-9_\-\.]{10,}["\']?'),
    
    # Generic API keys assignment patterns (e.g. key="secret")
    re.compile(r'(?i)(api[-_]?key|secret[-_]?key|password|passwd)\s*[:=]\s*["\']?[a-zA-Z0-9_\-\.]{8,}["\']?'),
    
    # Local/Internal Endpoints (localhost, 127.0.0.1, internal subnets)
    re.compile(r'(?i)https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})[^\s"\'<>]*'),
    
    # Windows and Unix absolute/local filesystem paths
    re.compile(r'(?:/[a-zA-Z0-9_\-]+)+/[a-zA-Z0-9_\-\.]+'), # Unix-like: /home/user/file.txt
    re.compile(r'(?i)[a-z]:\\(?:[^\\\s<>:"|?*]+\\)*[^\\\s<>:"|?*]+') # Windows-like: C:\path\to\file
]

def redact_sensitive_data(payload: dict) -> dict:
    """
    Recursively scans the export payload. 
    Redacts specific configured API keys AND generic high-risk infrastructure patterns.
    """
    # Load exact match strings from settings if available (your old logic)
    exact_secrets = set()
    try:
        with open("data/settings.json", "r") as f:
            settings = json.load(f)
            for value in settings.values():
                if isinstance(value, str) and len(value) > 4:
                    exact_secrets.add(value)
    except Exception:
        pass

    def scan_and_scrub(text: str) -> str:
        if not isinstance(text, str):
            return text

        for secret in exact_secrets:
            if secret in text:
                text = text.replace(secret, "[REDACTED]")

        for pattern in GENERIC_SENSITIVE_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
            
        return text

    if isinstance(payload, dict):
        return {k: scan_and_scrub(v) if isinstance(v, str) else redact_sensitive_data(v) for k, v in payload.items()}
    elif isinstance(payload, list):
        return [scan_and_scrub(item) if isinstance(item, str) else redact_sensitive_data(item) for item in payload]
    
    return payload

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
