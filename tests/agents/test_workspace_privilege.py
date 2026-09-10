from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.workspace_privilege import (
    HUMAN_WORKSPACE_MUTATING_OPS,
    human_workspace_writable,
    is_human_workspace_mutating_action,
    workspace_grants_for_turn,
)


def test_only_explicit_agent_toggle_grants_human_workspace_writes():
    assert human_workspace_writable(user_requested_agent=False) is False
    assert human_workspace_writable(user_requested_agent=True) is True


def test_chat_turn_drops_requested_workspace_grants():
    assert workspace_grants_for_turn(
        user_requested_agent=False,
        requested=("/Users/me/proj",),
    ) == ()


def test_agent_turn_keeps_requested_workspace_grants():
    assert workspace_grants_for_turn(
        user_requested_agent=True,
        requested=("/Users/me/proj",),
    ) == ("/Users/me/proj",)


def test_mutating_ops_are_human_workspace_writes():
    assert HUMAN_WORKSPACE_MUTATING_OPS == frozenset(
        {
            "notes.write",
            "documents.index",
            "mail.send",
            "calendar.write",
            "memory.write",
            "tasks.write",
        }
    )
    assert is_human_workspace_mutating_action("mail.send") is True
    assert is_human_workspace_mutating_action("notes.read") is False
    assert is_human_workspace_mutating_action("terminal") is False
    assert is_human_workspace_mutating_action(None) is False
