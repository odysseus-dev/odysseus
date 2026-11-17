from src.agent_loop import _state_manager_expected_action


def test_read_only_report_constraints_do_not_require_task_mutation():
    text = (
        "Prepare an operations report. Check the knowledge base for outdated "
        "documents needing update. Check scheduled task status and cron health. "
        "This is read-only: do not modify data, update tickets, or create anything."
    )

    assert _state_manager_expected_action(
        text, {"notes_calendar_tasks"}, {"manage_tasks"}
    ) == ("", set())


def test_explicit_scheduled_task_mutations_still_require_manager():
    assert _state_manager_expected_action(
        "Create a scheduled task for the daily report.",
        {"notes_calendar_tasks"},
        {"manage_tasks"},
    ) == ("manage_tasks", {"add", "create", "save"})

    tool, actions = _state_manager_expected_action(
        "Pause the scheduled task named Daily Report.",
        {"notes_calendar_tasks"},
        {"manage_tasks"},
    )
    assert tool == "manage_tasks"
    assert "pause" in actions
