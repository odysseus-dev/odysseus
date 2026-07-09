"""Regression: casual Agent-mode greetings must not crash intent classification.

The agent loop referenced ``_is_casual_low_signal`` while only defining the
regex constants it uses. Agent-mode greetings like "heyy" crashed before the
model could answer.
"""

from src.agent_loop import (
    _build_system_prompt,
    _classify_agent_request,
    _is_casual_low_signal,
)


def test_casual_low_signal_helper_handles_greetings():
    assert _is_casual_low_signal("heyy") is True
    assert _is_casual_low_signal("hey there") is True
    assert _is_casual_low_signal("hey open settings") is False


def test_casual_agent_request_classifies_without_name_error():
    intent = _classify_agent_request([{"role": "user", "content": "heyy"}], "heyy")

    assert intent["low_signal"] is True
    assert intent["domains"] == set()


def test_casual_agent_prompt_builds_without_suppress_skills_name_error():
    messages, mcp_schemas = _build_system_prompt(
        [{"role": "user", "content": "heyy"}],
        model="ornith-1.0-9b",
        active_document=None,
        mcp_mgr=None,
        disabled_tools=set(),
        needs_admin=False,
        relevant_tools={"ask_user", "update_plan"},
        suppress_local_context=True,
    )

    assert messages
    assert mcp_schemas == []


def test_agent_prompt_builds_with_active_email_without_name_error():
    messages, mcp_schemas = _build_system_prompt(
        [{"role": "user", "content": "reply saying thanks"}],
        model="ornith-1.0-9b",
        active_document=None,
        mcp_mgr=None,
        disabled_tools=set(),
        needs_admin=False,
        relevant_tools={"ask_user", "update_plan"},
        suppress_local_context=True,
        active_email={
            "uid": "123",
            "folder": "INBOX",
            "account": "",
            "subject": "Hello",
            "from": "sender@example.com",
            "body_preview": "Just checking in.",
        },
    )

    assert mcp_schemas == []
    assert any("ACTIVE EMAIL OPEN" in msg.get("content", "") for msg in messages)
