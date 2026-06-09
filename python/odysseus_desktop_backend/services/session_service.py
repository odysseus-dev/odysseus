from __future__ import annotations

import uuid
from typing import Any

from odysseus_desktop_backend.storage import Database, utc_ms


class SessionService:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT id, title, model, created_at, updated_at, last_message_at
            FROM sessions
            ORDER BY COALESCE(last_message_at, updated_at) DESC
            """
        ).fetchall()
        return self.db.rows_to_dicts(rows)

    def create(self, title: str | None = None, model: str | None = None) -> dict[str, Any]:
        now = utc_ms()
        session_id = str(uuid.uuid4())
        session_title = (title or "New chat").strip() or "New chat"
        session_model = (model or self.db.get_setting("default_model", "") or "").strip()
        self.db.conn.execute(
            """
            INSERT INTO sessions(id, title, model, created_at, updated_at, last_message_at)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (session_id, session_title, session_model, now, now),
        )
        self.db.conn.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> dict[str, Any]:
        row = self.db.conn.execute(
            """
            SELECT id, title, model, created_at, updated_at, last_message_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"session not found: {session_id}")
        return dict(row)

    def update(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {"title", "model"}
        fields = {key: value for key, value in updates.items() if key in allowed}
        if not fields:
            return self.get(session_id)
        now = utc_ms()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = [str(value) for value in fields.values()]
        values.extend([now, session_id])
        cur = self.db.conn.execute(
            f"UPDATE sessions SET {assignments}, updated_at = ? WHERE id = ?",
            values,
        )
        self.db.conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"session not found: {session_id}")
        return self.get(session_id)

    def delete(self, session_id: str) -> dict[str, Any]:
        cur = self.db.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.db.conn.commit()
        return {"deleted": cur.rowcount > 0, "session_id": session_id}

    def ensure(self, session_id: str | None, model: str | None = None) -> dict[str, Any]:
        if session_id:
            return self.get(session_id)
        return self.create(model=model)

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        if role not in {"system", "user", "assistant"}:
            raise ValueError("invalid message role")
        now = utc_ms()
        message_id = str(uuid.uuid4())
        self.db.conn.execute(
            """
            INSERT INTO messages(id, session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content, now),
        )
        self.db.conn.execute(
            """
            UPDATE sessions
            SET updated_at = ?, last_message_at = ?
            WHERE id = ?
            """,
            (now, now, session_id),
        )
        self.db.conn.commit()
        return {
            "id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": now,
        }

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        self.get(session_id)
        rows = self.db.conn.execute(
            """
            SELECT id, session_id, role, content, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
        return self.db.rows_to_dicts(rows)
