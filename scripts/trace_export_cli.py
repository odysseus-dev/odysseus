import json
import re
from typing import List
import os
import sys
import sqlite3

# Ensure the script can see core modules by adding the project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.database import Session as DbSession, ChatMessage as DbChatMessage
# Import Odysseus's session-maker configuration to drive build_trace_records
from core.database import SessionLocal 

# Adjust DB path relative to project root to guarantee runtime connectivity
DB_PATH = os.path.join(PROJECT_ROOT, "data", "app.db")

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

def redact_sensitive_data(payload):
    """
    Recursively scans the export payload. 
    Redacts specific configured API keys AND generic high-risk infrastructure patterns.
    """
    exact_secrets = set()
    try:
        with open(os.path.join(PROJECT_ROOT, "data", "settings.json"), "r") as f:
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

    # Base case: if it's a string, scan it
    if isinstance(payload, str):
        return scan_and_scrub(payload)
    
    # If it's a dictionary, recurse into values but leave keys intact
    elif isinstance(payload, dict):
        return {k: redact_sensitive_data(v) for k, v in payload.items()}
    
    # If it's a list or tuple, recurse into items
    elif isinstance(payload, (list, tuple)):
        return [redact_sensitive_data(item) for item in payload]
    
    # Base case: Return ints, floats, booleans, datetimes, and None as-is
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

def get_sessions():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            created_at,
            message_count,
            owner
        FROM sessions
        ORDER BY created_at DESC;
    """)

    sessions = cursor.fetchall()
    conn.close()
    return sessions

def get_messages(session_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            role,
            content,
            timestamp
        FROM chat_messages
        WHERE session_id = ?
        ORDER BY timestamp;
    """, (session_id,))

    rows = cursor.fetchall()
    conn.close()
    return rows

def parse_selection(selection, max_index, structured_pairs):
    """Maps the numeric selections from pair blocks back to the underlying messages."""
    selection = selection.strip().lower()

    if selection == "all":
        chosen_pairs = list(range(max_index))
    else:
        indices = set()
        for part in selection.split(","):
            part = part.strip()
            if "-" in part:
                start, end = map(int, part.split("-"))
                indices.update(range(start - 1, end))
            else:
                indices.add(int(part) - 1)
        chosen_pairs = sorted(i for i in indices if 0 <= i < max_index)

    # Gather the message IDs associated with the selected User/Assistant pairs
    target_msg_ids = []
    for pair_idx in chosen_pairs:
        pair_item = structured_pairs[pair_idx]
        target_msg_ids.append(pair_item["user_id"])
        target_msg_ids.append(pair_item["assistant_id"])
        
    return target_msg_ids

def main():
    while True:
        sessions = get_sessions()

        print("\nRecent Sessions\n")
        for i, s in enumerate(sessions, 1):
            print(f"{i}. {s['name']} ({s['message_count']} messages)")

        print("0. Exit")
        try:
            choice = int(input("\nSession: "))
        except ValueError:
            continue

        if choice == 0:
            break

        session = sessions[choice - 1]
        messages = get_messages(session["id"])

        print(f"\nSession: {session['name']}\n")

        # Step 1: Pair up consecutive user and assistant responses dynamically
        pairs = []
        last_user = None
        for msg in messages:
            if msg["role"] == "user":
                last_user = msg
            elif msg["role"] == "assistant" and last_user is not None:
                pairs.append({
                    "user_id": last_user["id"],
                    "user_content": last_user["content"],
                    "assistant_id": msg["id"],
                    "assistant_content": msg["content"]
                })
                last_user = None

        # Step 2: Print out the paired dialogues line by line
        for idx, pair in enumerate(pairs, 1):
            u_preview = pair["user_content"][:40].replace("\n", " ")
            a_preview = pair["assistant_content"][:40].replace("\n", " ")
            print(f"{idx:3}. 👤 User: {u_preview}")
            print(f"     🤖 Asst: {a_preview}\n")

        if not pairs:
            print("[-] No valid dialog pairs found in this session.")
            continue

        selection = input("Select pair index turns (all, 1-5, 3,7): ")
        message_ids = parse_selection(selection, len(pairs), pairs)

        # Step 3: Prompt for success/failure metrics and notes
        label = ""
        while label not in ["success", "failure"]:
            label = input("Label (success/failure): ").strip().lower()
            
        note = input("Note: ")

        # Step 4: Fire build_trace_records using local context details
        db_session = SessionLocal()
        try:
            final_json_payload = build_trace_records(
                db=db_session,
                current_user=session["owner"], # Dynamically infer owner from database record
                message_ids=message_ids,
                session_id=session["id"],
                label=label,
                note=note,
            )
            
            # Save the clean redacted payload to a clear local file
            output_filename = f"sanitized_session_{session['id'][:8]}.json"
            with open(output_filename, "w", encoding="utf-8") as f:
                json.dump(final_json_payload, f, indent=4, ensure_ascii=False, default=str) # <-- Added default=str here!
            print(f"[+] Local JSON exported successfully: {output_filename}")
            
        except Exception as e:
            print(f"[-] Execution Error: {e}")
        finally:
            db_session.close()

if __name__ == "__main__":
    main()