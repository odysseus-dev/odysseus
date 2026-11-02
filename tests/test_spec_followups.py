from pathlib import Path

import pytest

import routes.chat_helpers as chat_helpers
import src.llm_core as llm_core

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    {"title": "Qwen3.5 release notes", "url": "https://example.com/qwen", "snippet": "Improved reasoning and math."},
    {"title": "Qwen3.5 benchmarks", "url": "https://example.com/bench", "snippet": "New scores on reasoning tests."},
]


class FakeSession:
    endpoint_url = "http://model.local/v1/chat/completions"
    model = "test-model"
    headers = {"Authorization": "Bearer test"}


def _enable(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_SPEC_FOLLOWUPS", raising=False)
    monkeypatch.delenv("ODYSSEUS_FOLLOWUP_MODEL", raising=False)


def _fake_llm(monkeypatch, reply=None, error=None):
    calls = []

    async def fake(url, model, messages, **kwargs):
        calls.append({"url": url, "model": model, "messages": messages, "kwargs": kwargs})
        if error is not None:
            raise error
        return reply

    monkeypatch.setattr(llm_core, "llm_call_async", fake)
    return calls


@pytest.mark.asyncio
async def test_generates_contextual_questions(monkeypatch):
    _enable(monkeypatch)
    calls = _fake_llm(
        monkeypatch,
        "1. How does Qwen3.5 compare with GPT-5?\n"
        "2. What benchmarks improved?\n"
        "- What does the release note say about math?",
    )

    out = await chat_helpers.generate_followups(FakeSession(), "What's new in Qwen3.5?", SOURCES)

    assert [item["text"] for item in out] == [
        "How does Qwen3.5 compare with GPT-5?",
        "What benchmarks improved?",
        "What does the release note say about math?",
    ]
    assert len(calls) == 1
    assert calls[0]["kwargs"]["max_tokens"] >= 400
    assert "Do not include analysis" in calls[0]["messages"][0]["content"]
    assert "Qwen3.5" in calls[0]["messages"][1]["content"]
    assert "benchmarks" in calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_limit_and_dedupe_are_respected(monkeypatch):
    _enable(monkeypatch)
    _fake_llm(
        monkeypatch,
        "What changed?\nwhat changed?\nWhat does it cost?\nWho is it for?\nWhat is next?",
    )

    out = await chat_helpers.generate_followups(FakeSession(), "Qwen3.5", SOURCES, limit=2)

    assert [item["text"] for item in out] == ["What changed?", "What does it cost?"]


@pytest.mark.asyncio
async def test_disabled_by_env(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_SPEC_FOLLOWUPS", "0")
    calls = _fake_llm(monkeypatch, "What changed?")

    assert await chat_helpers.generate_followups(FakeSession(), "Qwen3.5", SOURCES) == []
    assert calls == []


@pytest.mark.asyncio
async def test_no_sources_means_no_request(monkeypatch):
    _enable(monkeypatch)
    calls = _fake_llm(monkeypatch, "What changed?")

    assert await chat_helpers.generate_followups(FakeSession(), "Qwen3.5", []) == []
    assert calls == []


@pytest.mark.asyncio
async def test_fails_open_on_model_error(monkeypatch):
    _enable(monkeypatch)
    _fake_llm(monkeypatch, error=TimeoutError("model slow"))

    assert await chat_helpers.generate_followups(FakeSession(), "Qwen3.5", SOURCES) == []


def test_browser_followup_added_when_browser_unused():
    items = [
        {"text": "How does Qwen3.5 compare with GPT-5?"},
        {"text": "What benchmarks improved?"},
        {"text": "What does the release note say about math?"},
    ]

    out = chat_helpers.add_browser_followup(items, {"tool_events": [{"tool": "web_search"}]})

    assert [item["text"] for item in out] == [
        "How does Qwen3.5 compare with GPT-5?",
        "What benchmarks improved?",
        "What does the release note say about math?",
        chat_helpers.BROWSER_FOLLOWUP_TEXT,
    ]
    assert out[-1]["action"] == "browser"


def test_browser_followup_not_added_when_browser_used():
    items = [{"text": "What changed?"}]

    for tool in ("private_browser", "mcp__builtin_browser__browser_click"):
        out = chat_helpers.add_browser_followup(items, {"tool_events": [{"tool": tool}]})
        assert out == items


def test_browser_followup_skips_duplicate_browser_question():
    items = [{"text": "Can you use the browser to compare them?"}]

    out = chat_helpers.add_browser_followup(items, {})

    assert out == items


def test_save_assistant_response_persists_followups(monkeypatch):
    import core.database as database

    picked = [{"text": "What benchmarks improved?"}]
    monkeypatch.setattr(database, "update_session_last_accessed", lambda *a, **k: None)
    monkeypatch.setattr(chat_helpers, "_append_sft_trace_record", lambda **k: None)

    class Message:
        def __init__(self, role, content, metadata=None):
            self.role = role
            self.content = content
            self.metadata = metadata or {}

    class Session:
        model = "test-model"

        def __init__(self):
            self.history = [Message("user", "What's new in Qwen3.5?")]

        def add_message(self, message):
            self.history.append(message)

    class Manager:
        def save_sessions(self):
            pass

    sess = Session()
    chat_helpers.save_assistant_response(
        sess,
        Manager(),
        "session-1",
        "Here is the answer.",
        {"model": "test-model"},
        web_sources=SOURCES,
        followups=picked,
    )

    assert sess.history[-1].metadata["followups"] == picked


def test_followup_chips_are_rendered_and_delegated():
    renderer = (ROOT / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")

    assert "export function buildFollowups" in renderer
    assert "followup-chip" in renderer
    assert "json.type === 'followups'" in chat
    assert "closest('.followup-chip')" in chat
