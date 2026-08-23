"""Regression tests for the native Windows Ollama desktop setup."""

from pathlib import Path

from src import llm_core, model_context


ROOT = Path(__file__).resolve().parents[1]


def test_local_ollama_connection_error_is_actionable():
    message = llm_core._unreachable_provider_message(
        "http://localhost:11434/v1/chat/completions"
    )
    assert "Ollama is not running" in message
    assert "relaunch Odysseus" in message


def test_other_provider_connection_error_stays_generic():
    message = llm_core._unreachable_provider_message(
        "https://api.example.test/v1/chat/completions"
    )
    assert message == "Cannot reach https://api.example.test"


def test_local_ollama_show_context_wins_over_family_default(monkeypatch):
    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "parameters": "num_ctx 98304\ntemperature 0.7",
                "model_info": {"qwen3.context_length": 131072},
            }

    monkeypatch.setattr(model_context.httpx, "post", lambda *args, **kwargs: Response())
    assert model_context._query_context_length(
        "http://localhost:11434/v1/chat/completions",
        "qwen3.5-9b-96k:latest",
    ) == (98304, True)


def test_desktop_launcher_starts_and_waits_for_ollama():
    cmd = (ROOT / "Start-Odysseus.cmd").read_text(encoding="utf-8")
    launcher = (ROOT / "Start-Odysseus.ps1").read_text(encoding="utf-8")
    assert "Start-Odysseus.ps1" in cmd
    assert 'ArgumentList @("serve")' in launcher
    assert "/api/version" in launcher
    assert "Wait-HttpReady" in launcher


def test_header_separates_chat_title_from_active_model():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    picker = (ROOT / "static" / "js" / "modelPicker.js").read_text(encoding="utf-8")
    sessions = (ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")
    assert 'id="current-meta" title="Chat title"' in page
    assert 'id="current-model-label"' in page
    assert "Active model:" in picker
    assert "const name = isIncognito ? 'Nobody' : 'New Chat';" in sessions
