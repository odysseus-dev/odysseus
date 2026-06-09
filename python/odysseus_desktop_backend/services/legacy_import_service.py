from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from odysseus_desktop_backend.logging_config import get_logger
from odysseus_desktop_backend.services.document_service import DocumentService, SUPPORTED_EXTENSIONS
from odysseus_desktop_backend.services.rag_service import RAGService
from odysseus_desktop_backend.services.session_service import SessionService
from odysseus_desktop_backend.services.settings_service import SettingsService


class LegacyImportService:
    def __init__(
        self,
        documents: DocumentService,
        rag: RAGService,
        sessions: SessionService,
        settings: SettingsService,
    ):
        self.documents = documents
        self.rag = rag
        self.sessions = sessions
        self.settings = settings

    def import_folder(self, folder: str) -> dict[str, list[dict[str, Any]]]:
        root = Path(folder).expanduser().resolve()
        logger.info("legacy import requested folder=%s", root)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Legacy folder not found: {folder}")

        data_dir = root if (root / "app.db").exists() else root / "data"
        report: dict[str, list[dict[str, Any]]] = {
            "imported": [],
            "skipped": [],
            "incompatible": [],
            "failed": [],
        }

        if not data_dir.exists():
            report["failed"].append(
                self._item("folder", str(root), "No app.db or data folder found in selected location.")
            )
            return report

        self._import_settings(data_dir, report)
        self._import_memory(data_dir, report)
        self._import_personal_docs(data_dir, report)
        self._import_database(data_dir / "app.db", report)
        logger.info(
            "legacy import complete folder=%s imported=%s skipped=%s incompatible=%s failed=%s",
            root,
            len(report["imported"]),
            len(report["skipped"]),
            len(report["incompatible"]),
            len(report["failed"]),
        )
        return report

    def _import_database(self, db_path: Path, report: dict[str, list[dict[str, Any]]]) -> None:
        if not db_path.exists():
            report["skipped"].append(self._item("database", str(db_path), "No legacy app.db found."))
            return

        try:
            legacy = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            legacy.row_factory = sqlite3.Row
        except Exception as exc:  # noqa: BLE001 - import report captures failure
            report["failed"].append(self._item("database", str(db_path), str(exc)))
            return

        try:
            self._import_sessions(legacy, report)
            self._import_db_documents(legacy, report)
            self._import_db_notes(legacy, report)
        finally:
            legacy.close()

    def _import_sessions(self, legacy: sqlite3.Connection, report: dict[str, list[dict[str, Any]]]) -> None:
        if not self._table_exists(legacy, "sessions") or not self._table_exists(legacy, "chat_messages"):
            report["skipped"].append(self._item("chats", "legacy database", "sessions/chat_messages tables not found."))
            return

        session_columns = self._columns(legacy, "sessions")
        message_columns = self._columns(legacy, "chat_messages")
        if "id" not in session_columns or "session_id" not in message_columns:
            report["incompatible"].append(self._item("chats", "legacy database", "chat schema is not recognized."))
            return

        title_col = "name" if "name" in session_columns else "title" if "title" in session_columns else None
        model_col = "model" if "model" in session_columns else None
        rows = legacy.execute("SELECT * FROM sessions").fetchall()
        imported = 0
        for row in rows:
            title = str(row[title_col] if title_col else "Imported chat") or "Imported chat"
            model = str(row[model_col] if model_col and row[model_col] else "")
            session = self.sessions.create(title=title, model=model)
            legacy_id = row["id"]
            order_col = "timestamp" if "timestamp" in message_columns else "created_at" if "created_at" in message_columns else "id"
            messages = legacy.execute(
                f"SELECT * FROM chat_messages WHERE session_id = ? ORDER BY {order_col}",
                (legacy_id,),
            ).fetchall()
            for message in messages:
                role = str(message["role"] if "role" in message_columns else "user")
                content = str(message["content"] if "content" in message_columns and message["content"] else "")
                if role not in {"system", "user", "assistant"} or not content:
                    continue
                self.sessions.add_message(session["id"], role, content)
            imported += 1
        report["imported"].append(self._item("chats", "legacy database", f"Imported {imported} chat session(s)."))

    def _import_db_documents(self, legacy: sqlite3.Connection, report: dict[str, list[dict[str, Any]]]) -> None:
        if not self._table_exists(legacy, "documents"):
            report["skipped"].append(self._item("documents", "legacy database", "documents table not found."))
            return
        columns = self._columns(legacy, "documents")
        if "current_content" not in columns:
            report["incompatible"].append(
                self._item("documents", "legacy database", "documents table has no current_content column.")
            )
            return
        title_col = "title" if "title" in columns else None
        rows = legacy.execute("SELECT * FROM documents").fetchall()
        imported = 0
        staging = self.documents.db.profile_dir / "imports" / "legacy-documents"
        staging.mkdir(parents=True, exist_ok=True)
        for row in rows:
            content = str(row["current_content"] or "")
            if not content.strip():
                continue
            title = str(row[title_col] if title_col and row[title_col] else f"legacy-document-{uuid.uuid4().hex[:8]}")
            path = staging / f"{self._safe_name(title)}.md"
            path.write_text(content, encoding="utf-8")
            try:
                document = self.documents.import_document(str(path))
                self.rag.index_document(document["id"])
                imported += 1
            except Exception as exc:  # noqa: BLE001
                report["failed"].append(self._item("document", title, str(exc)))
        report["imported"].append(self._item("documents", "legacy database", f"Imported {imported} document(s)."))

    def _import_db_notes(self, legacy: sqlite3.Connection, report: dict[str, list[dict[str, Any]]]) -> None:
        if not self._table_exists(legacy, "notes"):
            report["skipped"].append(self._item("notes", "legacy database", "notes table not found."))
            return
        columns = self._columns(legacy, "notes")
        content_col = "content" if "content" in columns else "text" if "text" in columns else None
        if not content_col:
            report["incompatible"].append(self._item("notes", "legacy database", "notes schema is not recognized."))
            return
        title_col = "title" if "title" in columns else None
        rows = legacy.execute("SELECT * FROM notes").fetchall()
        staging = self.documents.db.profile_dir / "imports" / "legacy-notes"
        staging.mkdir(parents=True, exist_ok=True)
        imported = 0
        for row in rows:
            content = str(row[content_col] or "")
            if not content.strip():
                continue
            title = str(row[title_col] if title_col and row[title_col] else f"legacy-note-{uuid.uuid4().hex[:8]}")
            path = staging / f"{self._safe_name(title)}.md"
            path.write_text(f"# {title}\n\n{content}", encoding="utf-8")
            try:
                document = self.documents.import_document(str(path))
                self.rag.index_document(document["id"])
                imported += 1
            except Exception as exc:  # noqa: BLE001
                report["failed"].append(self._item("note", title, str(exc)))
        report["imported"].append(self._item("notes", "legacy database", f"Imported {imported} note(s) as documents."))

    def _import_personal_docs(self, data_dir: Path, report: dict[str, list[dict[str, Any]]]) -> None:
        personal_docs = data_dir / "personal_docs"
        if not personal_docs.exists():
            report["skipped"].append(self._item("rag_sources", str(personal_docs), "personal_docs folder not found."))
            return
        imported = 0
        for path in personal_docs.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                report["skipped"].append(self._item("rag_source", str(path), "Unsupported file type."))
                continue
            try:
                document = self.documents.import_document(str(path))
                self.rag.index_document(document["id"])
                imported += 1
            except Exception as exc:  # noqa: BLE001
                report["failed"].append(self._item("rag_source", str(path), str(exc)))
        report["imported"].append(self._item("rag_sources", str(personal_docs), f"Imported {imported} source file(s)."))

    def _import_memory(self, data_dir: Path, report: dict[str, list[dict[str, Any]]]) -> None:
        memory_path = data_dir / "memory.json"
        if not memory_path.exists():
            report["skipped"].append(self._item("memory", str(memory_path), "memory.json not found."))
            return
        try:
            raw = json.loads(memory_path.read_text(encoding="utf-8"))
            lines = self._memory_lines(raw)
            if not lines:
                report["skipped"].append(self._item("memory", str(memory_path), "No compatible memory entries found."))
                return
            staging = self.documents.db.profile_dir / "imports" / "legacy-memory"
            staging.mkdir(parents=True, exist_ok=True)
            path = staging / "legacy-memory.md"
            path.write_text("# Legacy Memory\n\n" + "\n".join(f"- {line}" for line in lines), encoding="utf-8")
            document = self.documents.import_document(str(path))
            self.rag.index_document(document["id"])
            report["imported"].append(self._item("memory", str(memory_path), f"Imported {len(lines)} memory item(s)."))
        except Exception as exc:  # noqa: BLE001
            report["failed"].append(self._item("memory", str(memory_path), str(exc)))

    def _import_settings(self, data_dir: Path, report: dict[str, list[dict[str, Any]]]) -> None:
        settings_path = data_dir / "settings.json"
        if not settings_path.exists():
            report["skipped"].append(self._item("settings", str(settings_path), "settings.json not found."))
            return
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                report["incompatible"].append(self._item("settings", str(settings_path), "settings.json is not an object."))
                return
            compatible: dict[str, Any] = {}
            for key in ("default_model", "ollama_endpoint", "embedding_model"):
                if key in data:
                    compatible[key] = data[key]
            if compatible:
                self.settings.set(compatible)
                report["imported"].append(self._item("settings", str(settings_path), f"Imported {len(compatible)} setting(s)."))
            else:
                report["skipped"].append(self._item("settings", str(settings_path), "No compatible settings found."))
        except Exception as exc:  # noqa: BLE001
            report["failed"].append(self._item("settings", str(settings_path), str(exc)))

    def _memory_lines(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("memories") if isinstance(raw.get("memories"), list) else list(raw.values())
        else:
            return []
        lines: list[str] = []
        for item in items:
            if isinstance(item, str):
                lines.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("memory")
                if text:
                    lines.append(str(text))
        return lines

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _safe_name(self, name: str) -> str:
        keep = [ch if ch.isalnum() or ch in ("-", "_", " ") else "-" for ch in name]
        return ("".join(keep).strip() or "legacy-document")[:80]

    def _item(self, item_type: str, source: str, reason: str) -> dict[str, Any]:
        return {"type": item_type, "source": source, "reason": reason}


logger = get_logger("legacy_import")
