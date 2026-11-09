from src.agent_loop import _should_use_workspace_toolset


def test_sender_address_does_not_override_email_domain() -> None:
    assert not _should_use_workspace_toolset(
        "Search my email for messages from nora@harborimporters.example.",
        "/tmp/workspace",
        {"email", "web"},
    )


def test_calendar_date_does_not_look_like_named_machine() -> None:
    assert not _should_use_workspace_toolset(
        "List my calendar events on June 25, 2026.",
        "/tmp/workspace",
        {"notes_calendar_tasks"},
    )


def test_explicit_file_domain_still_selects_workspace_tools() -> None:
    assert _should_use_workspace_toolset(
        "Edit the notes.py file in this project and run its tests.",
        "/tmp/workspace",
        {"documents", "notes_calendar_tasks", "files"},
    )


def test_active_document_prevents_workspace_override() -> None:
    assert not _should_use_workspace_toolset(
        "Fix the function in this project.",
        "/tmp/workspace",
        {"files"},
        active_document_relevant=True,
    )
