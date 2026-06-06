import sqlite3
import json

def export_json(file_name: str, data: list[dict]):
    with open(f'{file_name}.json', 'w') as json_file:
        json.dump(data, json_file, indent=4, default=str)
    print(f"Data written to '{file_name}.json'")


# current_user will be passed from the router
def build_trace_records(
    current_user: str,
    session_id: str,
    message_ids: list[str],
    label: str,
):
    if not current_user:
        print("Error: No authenticated user found")
        return

    print(f"Exporting data for user: {current_user}\n")

    conn = sqlite3.connect("data/app.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Verify session ownership (do NOT export unrelated sessions)
    cur.execute("""
        SELECT id, name, model, message_count, created_at, owner
        FROM sessions
        WHERE id = ?
        AND (owner = ? OR owner IS NULL)
    """, (session_id, current_user))

    session = cur.fetchone()

    if not session:
        print("Error: Session not found or unauthorized")
        conn.close()
        return

    print(f"Processing session '{session_id}'\n")

    # Fetch only selected messages
    if not message_ids:
        print("Error: No message_ids provided")
        conn.close()
        return

    placeholders = ",".join(["?"] * len(message_ids))

    cur.execute(f"""
        SELECT role, content, timestamp, metadata, id
        FROM chat_messages
        WHERE session_id = ?
        AND id IN ({placeholders})
        ORDER BY timestamp
    """, (session_id, *message_ids))

    messages = cur.fetchall()

    messages_list = []
    for msg in messages:
        message_data = {
            "id": msg["id"],
            "role": msg["role"],
            "content": msg["content"],
            "timestamp": msg["timestamp"]
        }

        if msg["metadata"]:
            message_data["metadata"] = json.loads(msg["metadata"])

        messages_list.append(message_data)

    session_properties = {
        "name": session["name"],
        "id": session["id"],
        "owner": session["owner"],
        "model": session["model"],
        "message_count": session["message_count"],
        "created_at": session["created_at"],
        "label": label,
        "messages": messages_list
    }

    conn.close()

    export_json("chats_export", [session_properties])
    print(f"✓ Exported trace for session '{session_id}' user '{current_user}'")

# Usage in a FastAPI route:
# @router.post("/api/export-chats")
# async def export_chats(request: Request):
#     current_user = get_current_user(request)
#     build_trace_records(
#         current_user=current_user,
#         session_id="session-id",
#         message_ids=["msg-id"],
#         label="label"
#     )
#     return {"status": "exported"}