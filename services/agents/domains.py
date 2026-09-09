"""In-process domain adapters used by Odysseus MCP. No route imports."""

from __future__ import annotations

from typing import Any


class NotesService:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        note_id = arguments.get("note_id") or arguments.get("resource")
        if note_id:
            item = self.items.get(str(note_id).removeprefix("note:"))
            return {"notes": [item] if item and item.get("owner") == owner else []}
        return {"notes": [item for item in self.items.values() if item.get("owner") == owner]}

    def write(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        note_id = str(arguments.get("id") or arguments.get("note_id") or idempotency_key or "note-1")
        record = {"id": note_id, "owner": owner, **arguments}
        self.items[note_id] = record
        return record


class DocumentsService:
    def __init__(self) -> None:
        self.indexed: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        hits = [item for item in self.indexed if item.get("owner") == owner]
        if query:
            hits = [item for item in hits if query in str(item)]
        return {"documents": hits}

    def index(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, **arguments, "idempotency_key": idempotency_key}
        self.indexed.append(record)
        return record


class MailService:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.inbox: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [item for item in self.inbox if item.get("owner") == owner]}

    def send(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, **arguments}
        self.sent.append(record)
        return {"ok": True, "idempotency_key": idempotency_key}


class CalendarService:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"events": [item for item in self.events if item.get("owner") == owner]}

    def write(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, **arguments, "idempotency_key": idempotency_key}
        self.events.append(record)
        return record


class MemoryDomainService:
    def __init__(self) -> None:
        self.memories: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"memories": [item for item in self.memories if item.get("owner") == owner]}

    def write(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, **arguments, "idempotency_key": idempotency_key}
        self.memories.append(record)
        return record


class ResearchDomainService:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def invoke(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, "query": arguments.get("query"), "idempotency_key": idempotency_key}
        self.queries.append(record)
        return record


class TasksService:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"tasks": [item for item in self.tasks if item.get("owner") == owner]}

    def write(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, **arguments, "idempotency_key": idempotency_key}
        self.tasks.append(record)
        return record


class SessionsService:
    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"sessions": [item for item in self.sessions if item.get("owner") == owner]}


class GalleryService:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"items": [item for item in self.items if item.get("owner") == owner]}


class NotificationsService:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"notifications": [item for item in self.items if item.get("owner") == owner]}


class SkillsService:
    def __init__(self) -> None:
        self.skills: list[dict[str, Any]] = []
        self.invocations: list[dict[str, Any]] = []

    def read(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"skills": [item for item in self.skills if item.get("owner") == owner]}

    def invoke(self, owner: str, arguments: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        record = {"owner": owner, **arguments, "idempotency_key": idempotency_key}
        self.invocations.append(record)
        return record
