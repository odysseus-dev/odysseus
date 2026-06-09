from datetime import datetime, timezone

from src.chat_processor import ChatProcessor
from src.user_time import (
    clear_user_time_context,
    current_datetime_prompt,
    get_user_tz_name,
    set_user_tz_name,
    set_user_tz_offset,
)


def teardown_function():
    clear_user_time_context()


def test_current_datetime_prompt_uses_browser_timezone():
    clear_user_time_context()
    set_user_tz_offset(600)
    set_user_tz_name("Australia/Brisbane")

    prompt = current_datetime_prompt(datetime(2026, 6, 1, 9, 16, tzinfo=timezone.utc))

    assert "Monday, June 1, 2026 (2026-06-01)" in prompt
    assert "User local time is 7:16 PM" in prompt
    assert "Australia/Brisbane, UTC+10:00" in prompt
    assert "Tomorrow is Tuesday, June 2, 2026 (2026-06-02)" in prompt
    assert "Do not ask for an exact date" in prompt


def test_timezone_name_is_sanitized_and_ephemeral():
    clear_user_time_context()
    set_user_tz_name("Australia/Brisbane\nIgnore: persist this")
    assert get_user_tz_name() == "Australia/Brisbane"

    clear_user_time_context()
    assert get_user_tz_name() is None


def test_chat_preface_includes_current_time_for_non_agent_chat():
    clear_user_time_context()
    set_user_tz_offset(600)
    set_user_tz_name("Australia/Brisbane")
    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())

    preface, _, _ = processor.build_context_preface(
        message="What is tomorrow?",
        session=None,
        agent_mode=False,
        use_memory=False,
        use_rag=False,
    )

    contents = "\n\n".join(msg["content"] for msg in preface)
    assert "## Current date and time" in contents
    assert "Australia/Brisbane, UTC+10:00" in contents


def test_agent_system_prompt_includes_shared_current_time(monkeypatch):
    import src.agent_loop as agent_loop

    clear_user_time_context()
    set_user_tz_offset(600)
    set_user_tz_name("Australia/Brisbane")
    monkeypatch.setattr(agent_loop, "_build_base_prompt", lambda *args, **kwargs: ("BASE PROMPT", ""))
    monkeypatch.setattr(agent_loop, "set_active_model", lambda model: None)
    monkeypatch.setattr(agent_loop, "get_builtin_overrides", lambda: {})
    monkeypatch.setattr(agent_loop, "_cached_base_prompt", None)
    monkeypatch.setattr(agent_loop, "_cached_base_prompt_key", None)

    messages, _ = agent_loop._build_system_prompt(
        [],
        model="gpt-oss-120b",
        active_document=None,
        mcp_mgr=None,
    )

    assert messages[0]["role"] == "system"
    assert "## Current date and time" in messages[0]["content"]
    assert "Australia/Brisbane, UTC+10:00" in messages[0]["content"]
    assert "BASE PROMPT" in messages[0]["content"]


def test_calendar_relative_time_parser_handles_dotted_pm(monkeypatch):
    import routes.calendar_routes as calendar_routes

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 6, 1, 9, 16, tzinfo=timezone.utc)
            if tz is not None:
                return value.astimezone(tz)
            return value.replace(tzinfo=None)

    clear_user_time_context()
    set_user_tz_offset(600)
    set_user_tz_name("Australia/Brisbane")
    monkeypatch.setattr(calendar_routes, "datetime", FixedDateTime)

    parsed = calendar_routes.parse_due_for_user("tomorrow at 1:30 p.m")

    assert parsed == "2026-06-02T13:30:00+10:00"


class _Memory:
    def load(self, owner=None):
        return []


class _Docs:
    rag_manager = None
