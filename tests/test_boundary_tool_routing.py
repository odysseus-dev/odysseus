"""Regression tests for tool selection on implicit retrieval requests."""

from src.agent_loop import _classify_agent_request, _minimal_native_tool_prompt


def domains(prompt: str) -> set[str]:
    return set(_classify_agent_request([], prompt)["domains"])


def test_implicit_written_note_selects_notes_domain():
    assert "notes_calendar_tasks" in domains(
        "Find what I wrote down for roaster exhaust fan and say who to call if the noise persists."
    )


def test_saved_memory_search_selects_memory_domain():
    result = domains(
        "Search saved memory for Harbor Guji delay and report its fixture target code."
    )
    assert "memory" in result
    assert "web" not in result


def test_negated_workspace_reference_does_not_create_file_domain():
    result = domains(
        "Search saved memory for Harbor Guji delay. Do not use the workspace."
    )
    assert "memory" in result
    assert "files" not in result


def test_past_chat_search_selects_sessions_domain():
    assert "sessions" in domains(
        "Search prior chats for Farmers market prep and report the exact Guji bag count."
    )


def test_short_local_actions_do_not_take_casual_chat_path():
    assert "files" in domains("run tests")
    assert "files" in domains("read the README and summarize it")
    assert "files" in domains("find ajax local IP")
    assert "files" in domains("what is in my workspace?")


def test_local_data_filename_is_not_mistaken_for_a_web_domain():
    result = domains("Write answer.json")
    assert "web" not in result
    assert "files" in result
    assert "documents" not in result


def test_source_filename_selects_the_workspace_domain():
    assert "files" in domains("Create app.ts")
    assert "files" in domains("Edit src/app.py")


def test_short_casual_followup_stays_low_signal():
    result = _classify_agent_request([], "test now")
    assert result["low_signal"] is True
    assert result["domains"] == set()


def test_short_ambiguous_fragment_is_not_a_tool_retrieval_request():
    from src.agent_loop import _is_ambiguous_short_low_signal

    assert _is_ambiguous_short_low_signal("sned links") is True
    assert _is_ambiguous_short_low_signal("list my notes") is False
    assert _is_ambiguous_short_low_signal("run tests") is False


def test_minimal_prompt_requires_memory_tool_for_memory_lookup():
    prompt = _minimal_native_tool_prompt({"manage_memory"})
    assert "manage_memory" in prompt
    assert "injected memory context alone" in prompt


def test_minimal_prompt_requires_chat_search_for_past_chat_lookup():
    prompt = _minimal_native_tool_prompt({"search_chats"})
    assert "search_chats" in prompt
    assert "past chat or conversation" in prompt
