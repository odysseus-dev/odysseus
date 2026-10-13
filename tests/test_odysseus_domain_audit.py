from scripts.odysseus_domain_audit import Case, _durable_tool_events, prompt_matrix, score_case
from scripts.odysseus_related_flow_audit import (
    Flow,
    FlowTurn,
    _flow_has_good_training_shape,
    _provider_failure,
    _score_turn,
)
from scripts.odysseus_remaining_tool_audit import REMAINING_TOOLS, SFT_POLICY_DISABLED_TOOLS, tool_matrix
from src.tool_index import ToolIndex
from src.agent_loop import _SFT_DISABLED_WORKSPACE_TOOLS, _STATEFUL_TOOL_CARRYOVER_DOMAINS


def test_domain_matrix_has_twenty_cases_per_domain():
    matrix = prompt_matrix()
    assert set(matrix) == {"skills", "tasks", "theme", "memory", "documents", "cookbook"}
    assert {name: len(cases) for name, cases in matrix.items()} == {
        "skills": 20,
        "tasks": 20,
        "theme": 20,
        "memory": 20,
        "documents": 20,
        "cookbook": 20,
    }
    assert len({case.id for cases in matrix.values() for case in cases}) == 120


def test_cookbook_cases_reject_mutating_tools():
    case = Case("cookbook_test", "find a model", ("search_hf_models",), dry_run=True)
    events = [{"type": "tool_start", "tool": "serve_model"}, {"type": "tool_output", "tool": "serve_model", "output": "started"}]
    result = score_case(case, events, "I started it")
    assert result["dry_run_ok"] is False
    assert result["pass"] is False


def test_score_rejects_raw_tool_dump_and_unavailable_claim():
    case = Case("memory_test", "list memories", ("manage_memory",))
    events = [{"type": "tool_start", "tool": "manage_memory"}, {"type": "tool_output", "tool": "manage_memory", "output": "ok"}]
    result = score_case(case, events, 'I don\'t have a manage_memory tool.')
    assert result["response_ok"] is False
    assert result["pass"] is False


def test_score_accepts_clean_named_tool_trace():
    case = Case("tasks_test", "list tasks", ("manage_tasks",))
    events = [{"type": "tool_start", "tool": "manage_tasks"}, {"type": "tool_output", "tool": "manage_tasks", "output": "Found 0 tasks"}]
    result = score_case(case, events, "You have no scheduled tasks.")
    assert result["pass"] is True


def test_skill_keyword_fallback_keeps_registry_tool_available():
    matching = [tools for keywords, tools in ToolIndex._KEYWORD_HINTS.items()
                if any(keyword in "list my skills" for keyword in keywords)]
    assert matching
    assert any("manage_skills" in tools for tools in matching)


def test_stateful_domains_carry_one_round_after_use():
    assert _STATEFUL_TOOL_CARRYOVER_DOMAINS["manage_skills"] == "skills"
    assert _STATEFUL_TOOL_CARRYOVER_DOMAINS["manage_memory"] == "memory"
    assert _STATEFUL_TOOL_CARRYOVER_DOMAINS["manage_documents"] == "documents"
    assert _STATEFUL_TOOL_CARRYOVER_DOMAINS["manage_tasks"] == "notes_calendar_tasks"
    assert _STATEFUL_TOOL_CARRYOVER_DOMAINS["ui_control"] == "ui"
    assert _STATEFUL_TOOL_CARRYOVER_DOMAINS["list_served_models"] == "cookbook"


def test_durable_history_is_the_tool_event_source_for_scoring():
    history = {
        "history": [{
            "role": "assistant",
            "content": "Done.",
            "metadata": {"tool_events": [{"tool": "manage_tasks", "command": '{"action":"list"}'}]},
        }]
    }
    assert _durable_tool_events(history)[0]["tool"] == "manage_tasks"


def test_related_flow_shape_rejects_false_failure_claims():
    history = {
        "history": [
            {"role": "user", "content": "Update that skill."},
            {
                "role": "assistant",
                "content": "The skill registry may have failed, but I updated it.",
                "metadata": {"tool_events": [{"tool": "manage_skills"}]},
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert reasons == ["turn 1 contains a false/ambiguous failure claim"]


def test_related_flow_shape_accepts_verifiability_language_with_cant():
    history = {
        "history": [
            {"role": "user", "content": "Ask the teacher to check the rewrite."},
            {
                "role": "assistant",
                "content": (
                    "The teacher used the tool result. If the evidence can't be "
                    "checked independently of the assistant's claim, it is not grounded."
                ),
                "metadata": {"tool_events": [{"tool": "ask_teacher", "output": "Valid review"}]},
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is True
    assert reasons == []


def test_related_flow_shape_rejects_persisted_tool_errors():
    history = {
        "history": [
            {"role": "user", "content": "Draft a reply."},
            {
                "role": "assistant",
                "content": "Reply draft opened. Nothing has been sent.",
                "metadata": {
                    "tool_events": [
                        {
                            "tool": "mcp__email__draft_email_reply",
                            "output": "Error: [Errno 111] Connection refused",
                        }
                    ]
                },
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert reasons == ["turn 1 has failed tool output from mcp__email__draft_email_reply"]


def test_related_flow_shape_rejects_missing_just_saved_memory():
    history = {
        "history": [
            {"role": "user", "content": "Find the memory you just saved about marker audit-123."},
            {
                "role": "assistant",
                "content": "No memories found matching 'audit-123'.",
                "metadata": {"tool_events": [{"tool": "manage_memory", "output": "No memories found"}]},
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert reasons == ["turn 1 failed to find the just-saved memory"]


def test_related_flow_shape_rejects_repeated_explicit_tool_loop():
    history = {
        "history": [
            {"role": "user", "content": "Show which agent tools are currently disabled."},
            {
                "role": "assistant",
                "content": (
                    "No agent tools are currently disabled, so every core tool domain is enabled. "
                    "No agent tools are currently disabled, so every core tool domain is enabled."
                ),
                "metadata": {
                    "tool_events": [
                        {"tool": "manage_settings", "command": '{"action":"list_tools"}', "output": "ok"},
                        {"tool": "manage_settings", "command": '{"action":"list_tools"}', "output": "ok"},
                    ],
                    "round_texts": [
                        "No agent tools are currently disabled.",
                        "No agent tools are currently disabled.",
                    ],
                },
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert "turn 1 repeated 1 identical tool call(s)" in reasons
    assert "turn 1 has multiple non-empty assistant rounds" in reasons
    assert "turn 1 appears to concatenate repeated assistant answers" in reasons


def test_related_flow_shape_accepts_cumulative_progress_rounds():
    preamble = "I'll check the inbox first."
    final = preamble + " The inbox is clean; Junk contains one suspicious message."
    history = {
        "history": [
            {"role": "user", "content": "Check Inbox and Junk for spam."},
            {
                "role": "assistant",
                "content": final,
                "metadata": {
                    "tool_events": [
                        {"tool": "mcp__email__scan_spam", "command": '{"folder":"INBOX"}', "output": "clean"},
                        {"tool": "mcp__email__scan_spam", "command": '{"folder":"Junk"}', "output": "one candidate"},
                    ],
                    "round_texts": [preamble, final],
                },
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is True
    assert reasons == []


def test_related_flow_shape_rejects_missing_just_created_chat():
    history = {
        "history": [
            {"role": "user", "content": "Find the audit helper abc chat in my chat list."},
            {
                "role": "assistant",
                "content": "No sessions found matching 'audit abc'.",
                "metadata": {"tool_events": [{"tool": "list_sessions", "output": "No sessions found"}]},
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert reasons == ["turn 1 failed to find the just-created chat"]


def test_related_flow_shape_rejects_raw_calendar_event_dump():
    history = {
        "history": [
            {"role": "user", "content": "What events are visible for the next week?"},
            {
                "role": "assistant",
                "content": (
                    "Here are your events (2):\n"
                    "- [Stretch](#event-a) — 2026-08-28T07:30:00Z -> 2026-08-28T08:00:00Z #health\n"
                    "- [Haircut](#event-b) — 2026-08-28T09:00:00Z -> 2026-08-28T10:00:00Z #personal"
                ),
                "metadata": {"tool_events": [{"tool": "manage_calendar", "output": "ok"}]},
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert reasons == ["turn 1 appears to preserve a raw harness dump"]


def test_related_flow_score_requires_compound_tools_for_notes_handoff():
    flow = Flow(
        "ui_calendar_notes_context",
        "notes",
        "UI panel context handoff",
        (FlowTurn("open_notes", "Open notes and create a note", ("ui_control", "manage_notes")),),
    )
    events = [
        {"type": "tool_start", "tool": "ui_control", "command": "open_panel notes"},
        {"type": "tool_output", "tool": "ui_control", "output": "Opening notes panel"},
    ]

    result = _score_turn(flow, flow.turns[0], events, "The notes panel is open.")

    assert result["pass"] is False
    assert result["missing_required_tools"] == ["manage_notes"]


def test_related_flow_classifies_provider_status_as_infrastructure_failure():
    events = [{"type": "error", "error": "Read timeout", "status": 504}]

    assert _provider_failure(events) is True


def test_related_flow_classifies_provider_cooldown_as_infrastructure_failure():
    events = [{
        "type": "error",
        "error": "Upstream https://openrouter.ai unreachable (cooldown active)",
        "status": 503,
    }]

    assert _provider_failure(events, "The model provider returned no usable output.") is True


def test_related_flow_classifies_missing_enabled_endpoint_as_infrastructure_failure():
    events = [{
        "type": "tool_output",
        "tool": "generate_image",
        "output": "Error: No enabled endpoints found",
        "exit_code": 0,
    }]

    assert _provider_failure(events) is True


def test_related_flow_does_not_classify_tool_miss_as_provider_failure():
    events = [{"type": "tool_output", "tool": "cancel_download", "output": "No download found"}]

    assert _provider_failure(events, "I could not find that download.") is False


def test_sft_workspace_filter_keeps_private_skill_registry():
    assert "manage_skills" not in _SFT_DISABLED_WORKSPACE_TOOLS


def test_remaining_tool_matrix_has_twenty_cases_per_tool():
    matrix = tool_matrix()
    assert set(matrix) == set(REMAINING_TOOLS)
    assert all(len(cases) == 20 for cases in matrix.values())
    assert len({case.id for cases in matrix.values() for case in cases}) == 20 * len(REMAINING_TOOLS)


def test_remaining_mutation_cases_are_dry_run():
    matrix = tool_matrix()
    for tool in ("serve_model", "download_model", "stop_served_model", "bulk_email"):
        assert all(case.dry_run for case in matrix[tool])


def test_workspace_tools_are_explicitly_separated_from_sft_audit():
    assert SFT_POLICY_DISABLED_TOOLS <= set(REMAINING_TOOLS)


def test_remaining_tool_policy_disabled_list_documents_sft_workspace_gap():
    assert "bash" not in SFT_POLICY_DISABLED_TOOLS
    for tool in ("python", "read_file", "write_file", "edit_file", "apply_patch"):
        assert tool in SFT_POLICY_DISABLED_TOOLS


def test_related_flow_shape_rejects_failed_to_tool_output():
    from scripts.odysseus_related_flow_audit import _flow_has_good_training_shape

    history = {
        "history": [
            {"role": "user", "content": "Send it"},
            {
                "role": "assistant",
                "content": "Done",
                "metadata": {
                    "tool_events": [{
                        "tool": "send_to_session",
                        "output": "Failed to send to session: provider rejected credentials",
                    }]
                },
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert any("failed tool output" in reason for reason in reasons)


def test_related_flow_shape_rejects_unconfigured_teacher_output():
    history = {
        "history": [
            {"role": "user", "content": "Ask the teacher"},
            {
                "role": "assistant",
                "content": "The teacher is unavailable.",
                "metadata": {
                    "tool_events": [{
                        "tool": "ask_teacher",
                        "output": "No teacher model configured. Specify a model name.",
                        "exit_code": None,
                    }]
                },
            },
        ]
    }

    ok, reasons = _flow_has_good_training_shape(history, 1)

    assert ok is False
    assert reasons == ["turn 1 has failed tool output from ask_teacher"]
