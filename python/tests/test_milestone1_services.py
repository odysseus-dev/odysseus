from __future__ import annotations

from pathlib import Path

from odysseus_desktop_backend.services.chat_service import ChatService
from odysseus_desktop_backend.services.model_service import ModelService
from odysseus_desktop_backend.services.session_service import SessionService
from odysseus_desktop_backend.services.settings_service import SettingsService
from odysseus_desktop_backend.storage import Database


class FakeModelService(ModelService):
    def __init__(self, db: Database):
        super().__init__(db)
        self.calls = []

    def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        self.calls.append({"model": model, "messages": messages})
        return "assistant reply"


def build_services(profile_dir: Path):
    db = Database(profile_dir)
    settings = SettingsService(db)
    sessions = SessionService(db)
    models = FakeModelService(db)
    chat = ChatService(sessions, settings, models)
    return db, settings, sessions, models, chat


def test_settings_sessions_and_messages_persist(tmp_path: Path):
    db, settings, sessions, _models, chat = build_services(tmp_path)
    settings.set({"default_model": "llama3.2:latest"})
    result = chat.send("Hello desktop")
    session_id = result["session"]["id"]
    db.close()

    reopened = Database(tmp_path)
    reopened_sessions = SessionService(reopened)
    reopened_settings = SettingsService(reopened)

    assert reopened_settings.get()["default_model"] == "llama3.2:latest"
    assert reopened_sessions.list()[0]["id"] == session_id
    assert [item["role"] for item in reopened_sessions.messages(session_id)] == [
        "user",
        "assistant",
    ]
    reopened.close()


def test_chat_is_simple_non_rag_ollama_call(tmp_path: Path):
    db, settings, _sessions, models, chat = build_services(tmp_path)
    settings.set({"default_model": "mistral"})

    result = chat.send("No tools or retrieval here")

    assert result["assistant_message"]["content"] == "assistant reply"
    assert result["retrieved_chunks"] == []
    assert models.calls == [
        {
            "model": "mistral",
            "messages": [
                {"role": "user", "content": "No tools or retrieval here"},
            ],
        }
    ]
    db.close()
