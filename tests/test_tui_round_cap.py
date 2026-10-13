"""TUI agent turns use short lookup limits and adaptive coding turns."""

import json

from src.tool_policy import WEB_TOOL_NAMES
from routes.chat_routes import (
    _effective_agent_rounds,
    _effective_native_output_tokens,
    _parse_client_runtime_context,
)
from src.agent_loop import (
    _looks_like_local_computer_request,
    _looks_like_explicit_tui_personal_domain_request,
    _looks_like_explicit_tui_app_or_external_request,
    _is_qwen_explicit_model_list_request,
    _is_qwen_explicit_latest_email_request,
    _route_tui_local_workspace_tools,
    _tui_local_fallback_shell_command,
    _tui_recover_invalid_local_tools,
    ToolBlock,
    _looks_like_malformed_tui_tool_call,
    _tui_project_discovery_summary,
    _parse_qwen_explicit_note_view,
    _ody_qwen_terminal_tool_summary,
    _tui_normalize_network_host_command,
    _tui_normalize_workspace_host_command,
    _tui_network_summary,
    _tui_local_workspace_turn,
    _web_search_unavailable_for_turn,
    _is_host_bridge_failure_result,
    _empty_response_fallback,
    _normalize_ody_qwen_text_artifacts,
    _workspace_coding_rules,
    _looks_like_workspace_coding_request,
    _workspace_pre_mutation_verification_block,
    _tui_local_test_runner_command,
    _tui_local_test_runner_host_shell_content,
    _tui_local_smoke_test_runner_command,
    _tui_normalize_pytest_command,
    _tui_smoke_test_request,
    _tui_explicit_full_test_request,
    _calendar_context_owns_ambiguous_mutation,
    _calendar_lookup_requires_fresh_tool,
    _parse_simple_calendar_tool_request,
    _memory_search_precedes_unrequested_list,
)


def test_all_tui_bridge_mutation_failures_are_terminal_transport_errors():
    for result in (
        {"error": "apply_patch: host bridge request failed: connection refused"},
        {"error": "edit_file: bridge returned HTTP 502"},
        {"error": "write_file: bridge returned invalid payload"},
        {"output": "host_shell: no TUI host bridge advertised", "exit_code": 1},
        {"stderr": "host_shell: missing TUI host bridge", "exit_code": 1},
        {"error": "host bridge unavailable", "exit_code": 1},
    ):
        assert _is_host_bridge_failure_result(result)


def test_tui_non_coding_turn_caps_stale_persisted_setting():
    assert _effective_agent_rounds(100, {"surface": "odysseus-tui"}, 20) == 20
    assert _effective_agent_rounds("bad", {"surface": "odysseus-tui"}, 20) == 20


def test_tui_coding_turn_uses_adaptive_stopping():
    prompt = (
        "Inspect this local repository, repair the parser, and run the focused tests."
    )
    assert _effective_agent_rounds(
        100,
        {"surface": "odysseus-tui"},
        20,
        message=prompt,
    ) is None


def test_tui_read_only_turn_keeps_short_round_cap():
    assert _effective_agent_rounds(
        100,
        {"surface": "odysseus-tui"},
        20,
        message="Inspect this local repository and report its top-level files.",
    ) == 20


def test_existing_file_edits_allow_one_baseline_verification_command():
    assert _workspace_pre_mutation_verification_block(
        ToolBlock("host_shell", json.dumps({"command": "pytest -q tests/test_parser.py"}))
    )
    assert _workspace_pre_mutation_verification_block(
        ToolBlock("bash", "python -m pytest -q")
    )
    assert not _workspace_pre_mutation_verification_block(
        ToolBlock("host_shell", json.dumps({"command": "pwd && ls"}))
    )
    assert not _workspace_pre_mutation_verification_block(
        ToolBlock("edit_file", "config.py")
    )


def test_webui_non_workspace_turn_keeps_configured_round_limit():
    assert _effective_agent_rounds(100, {"surface": "webui"}, 20) == 100


def test_unattended_native_terminal_agent_honors_explicit_round_budget():
    assert _effective_agent_rounds(
        6,
        {
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
            "max_agent_rounds": 30,
        },
        20,
    ) == 30


def test_unattended_native_terminal_agent_without_budget_is_finite():
    assert _effective_agent_rounds(
        6,
        {
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
        },
        20,
    ) == 6


def test_attended_native_terminal_agent_keeps_configured_round_limit():
    assert _effective_agent_rounds(
        6,
        {
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": False,
        },
        20,
    ) == 6


def test_unattended_native_runtime_can_bound_output_without_changing_ui_preset():
    native = {
        "surface": "odysseus-native",
        "terminal_agent": True,
        "unattended_mode": True,
        "max_output_tokens": 8192,
    }

    assert _effective_native_output_tokens(32768, native) == 8192
    assert _effective_native_output_tokens(
        32768, {"surface": "webui", "max_output_tokens": 512}
    ) == 32768
    assert _effective_native_output_tokens(
        32768, {**native, "max_output_tokens": "bad"}
    ) == 32768
    assert _effective_native_output_tokens(
        32768, {**native, "max_output_tokens": 1}
    ) == 256


def test_native_runtime_context_preserves_only_a_bounded_output_cap():
    context = _parse_client_runtime_context({
        "surface": "odysseus-native",
        "terminal_agent": True,
        "unattended_mode": True,
        "max_output_tokens": 8192,
    })
    assert context["max_output_tokens"] == 8192
    assert _parse_client_runtime_context({
        "surface": "odysseus-tui",
        "terminal_agent": True,
        "unattended_mode": True,
        "max_output_tokens": 8192,
    }).get("max_output_tokens") is None


def test_webui_workspace_coding_turn_uses_adaptive_stopping():
    assert _effective_agent_rounds(
        20,
        {"surface": "webui"},
        20,
        message="Repair the parser and verify the change.",
        workspace_agent_intent=True,
    ) is None


def test_network_prompts_are_classified_as_local_computer_work():
    for prompt in (
        "Find the local IP to SSH into ajax.",
        "Resolve ajax on the local network and tell me its IP.",
        "Tailscale is down; resolve ajax on the LAN.",
        "Inspect DNS and the default route on this machine.",
    ):
        assert _looks_like_local_computer_request(prompt), prompt


def test_network_lookup_replaces_ping_with_address_evidence():
    normalized = _tui_normalize_network_host_command(
        "ping -c 4 ajax.local",
        "Resolve ajax on the local network and tell me its IP.",
    )
    assert normalized is not None
    command, reason = normalized
    assert "getent hosts ajax.local" in command
    assert "ip -o -4 addr show" in command
    assert "ip route show default" in command
    assert "ping replaced" in reason


def test_network_connectivity_request_preserves_ping():
    assert _tui_normalize_network_host_command(
        "ping -c 4 ajax.local",
        "Ping ajax to test connectivity.",
    ) is None


def test_network_fallback_includes_named_target_without_guessing_hosts():
    command = _tui_local_fallback_shell_command(
        "Find the local IP for ajax so I can SSH to it; Tailscale is down."
    )
    assert command is not None
    assert "getent hosts ajax" in command
    assert "ip route show default" in command
    assert _tui_local_fallback_shell_command(
        "Inspect the local network interfaces."
    ) == "ip -o -4 addr show; ip route show default"


def test_stale_read_tools_on_coding_turn_recover_to_one_host_probe():
    blocks, recovered = _tui_recover_invalid_local_tools(
        [
            ToolBlock("get_workspace", "{}"),
            ToolBlock("ls", "{}"),
        ],
        "Fix the parser in this local TUI project.",
    )

    assert recovered is True
    assert [block.tool_type for block in blocks] == ["host_shell"]
    assert "git_root=" in json.loads(blocks[0].content)["command"]


def test_stale_mutating_tool_is_not_rewritten_to_shell():
    blocks, recovered = _tui_recover_invalid_local_tools(
        [ToolBlock("manage_notes", "{}")],
        "Fix the parser in this local TUI project and run the tests.",
    )

    assert recovered is False
    assert blocks == []


def test_workspace_host_command_replaces_metadata_placeholder():
    normalized = _tui_normalize_workspace_host_command(
        "cd session_cwd && npm test",
        "/tmp/my project",
    )

    assert normalized == (
        "cd '/tmp/my project' && npm test",
        "metadata workspace placeholder replaced with active session cwd",
    )


def test_malformed_qwen_local_tool_markup_is_detected_narrowly():
    assert _looks_like_malformed_tui_tool_call(
        "parameter=hos_shell parameter=command cd /repo python -m pytest"
    )
    assert _looks_like_malformed_tui_tool_call(
        "hos_shell\ncommand\nls -la\n</command\n</hos_shell\n</"
    )
    assert not _looks_like_malformed_tui_tool_call(
        "I can help with your host shell configuration."
    )


def test_tool_failure_is_not_followed_by_a_blank_answer():
    final_response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[
            {
                "tool": "manage_notes",
                "output": "Note 'missing' not found",
                "exit_code": 1,
            }
        ],
    )
    assert final_response == "manage_notes failed: Note 'missing' not found"
    assert "manage_notes failed" in (chunk or "")


def test_successful_host_shell_output_is_not_lost_to_blank_followup():
    final_response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[
            {"tool": "host_shell", "output": "No supported test runner found", "exit_code": 0}
        ],
    )
    assert final_response == "No supported test runner found"
    assert "No supported test runner found" in (chunk or "")


def test_successful_calendar_output_is_not_lost_to_blank_followup():
    final_response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[
            {
                "tool": "manage_calendar",
                "command": '{"action":"list_events"}',
                "output": (
                    "Found 1 event:\n"
                    "- 2026-09-01T10:00:00 -> 2026-09-01T11:00:00: "
                    "[Planning review](#event-planning-review)"
                ),
                "exit_code": 0,
            }
        ],
    )
    assert "Planning review" in final_response
    assert '"type": "final_response"' in (chunk or "")


def test_calendar_panel_open_beats_context_snapshot_for_blank_followup():
    final_response, _ = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[
            {
                "tool": "ui_control",
                "command": "open_panel calendar",
                "output": "",
                "exit_code": 0,
            },
            {
                "tool": "manage_calendar",
                "command": '{"action":"list_events"}',
                "output": (
                    "Found 1 event:\n"
                    "- 2026-09-01T10:00:00 -> 2026-09-01T11:00:00: "
                    "[Planning review](#event-planning-review)"
                ),
                "exit_code": 0,
                "context_only": True,
            },
        ],
    )
    assert final_response == "The calendar panel is open."


def test_calendar_lookup_parser_does_not_override_create_then_show_request():
    from src.agent_loop import _parse_simple_calendar_tool_request

    assert _parse_simple_calendar_tool_request(
        'Create a one-hour event called "Ablation review" tomorrow at 2pm, '
        "then show the calendar again so I can verify it."
    ) is None


def test_calendar_bounds_include_both_today_and_tomorrow():
    from src.agent_loop import _calendar_bounds_for_prompt

    assert _calendar_bounds_for_prompt(
        "What's scheduled for today and tomorrow?", today="2026-08-30"
    ) == ("2026-08-30", "2026-09-01")


def test_recent_calendar_anchor_owns_ambiguous_delete_entry_followup():
    messages = [{
        "role": "assistant",
        "content": (
            "View event: [Forecast lock prep, 9:00 AM]"
            "(#event-12345678-abcd-4321-abcd-123456789abc)"
        ),
    }]
    assert _calendar_context_owns_ambiguous_mutation(
        "I finished early - go ahead and delete the prep entry.", messages
    )


def test_explicit_task_language_overrides_recent_calendar_anchor():
    messages = [{
        "role": "assistant",
        "content": (
            "View event: [Forecast lock prep, 9:00 AM]"
            "(#event-12345678-abcd-4321-abcd-123456789abc)"
        ),
    }]
    assert not _calendar_context_owns_ambiguous_mutation(
        "Delete the scheduled task instead.", messages
    )


def test_contextual_calendar_entry_lookup_requires_fresh_tool_and_title_query():
    messages = [{
        "role": "assistant",
        "content": (
            "View event: [Forecast scenario prep, 9:00 AM]"
            "(#event-12345678-abcd-4321-abcd-123456789abc)"
        ),
    }]
    prompt = "Show me that prep entry so I can confirm it doesn't collide."
    assert _calendar_lookup_requires_fresh_tool(
        prompt,
        {"notes_calendar_tasks"},
        {"manage_calendar"},
        messages,
    )
    tool, command = _parse_simple_calendar_tool_request(prompt, messages)
    assert tool == "manage_calendar"
    assert json.loads(command)["query"] == "Forecast scenario prep"


def test_memory_search_blocks_unrequested_full_list_escalation():
    events = [{
        "tool": "manage_memory",
        "command": "search\nphone number",
        "output": "No memories found matching 'phone number'.",
        "exit_code": 0,
    }]
    assert _memory_search_precedes_unrequested_list(events, False)
    assert not _memory_search_precedes_unrequested_list(events, True)


def test_explicit_note_contents_request_extracts_title_for_locator_followup():
    assert _parse_qwen_explicit_note_view(
        "Find my note called Japan, then show me its contents."
    ) == "Japan"
    assert _parse_qwen_explicit_note_view("Find my note called Japan") is None


def test_note_view_summary_preserves_body_content():
    assert _ody_qwen_terminal_tool_summary({
        "tool": "manage_notes",
        "command": '{"action":"view","id":"abc123"}',
        "output": "AI: - [abc123] **Japan**\nTokyo itinerary",
    }) == "- [abc123] **Japan**\nTokyo itinerary"


def test_test_request_gets_a_bounded_project_runner_fallback():
    command = _tui_local_fallback_shell_command("Run the tests in this repo")
    assert command is not None
    assert "runner=.venv/bin/python" in command
    assert "runner=venv/bin/python" in command
    assert "else runner=python" in command
    assert '"$runner" -m pytest -q' in command
    assert "tests/test_tui_round_cap.py" not in command
    assert "npm test" in command
    assert "find ." not in command


def test_tui_test_runner_prefers_workspace_python_environment():
    command = _tui_local_test_runner_command()
    assert "[ -x .venv/bin/python ]" in command
    assert "runner=.venv/bin/python" in command
    assert "[ -x venv/bin/python ]" in command
    assert "runner=venv/bin/python" in command
    assert "git rev-parse --path-format=absolute --git-common-dir" in command
    assert '$(dirname "$git_common")/.venv/bin/python' in command
    assert "else runner=python" in command
    assert '"$runner" -m pytest -q' in command
    assert "git diff --name-only --diff-filter=ACMR HEAD" in command
    assert 'tests/test_${stem}.py' in command


def test_tui_smoke_test_runner_is_repository_agnostic():
    assert _tui_smoke_test_request("test now")
    assert _tui_smoke_test_request("Run tests now")
    assert _tui_smoke_test_request("Run a quick smoke test in this repo")
    assert _tui_smoke_test_request("Run tests, but keep it bounded and small")
    assert not _tui_smoke_test_request("Run the tests in this repo")

    command = _tui_local_smoke_test_runner_command()
    assert "tests/test_tui_round_cap.py" not in command
    assert "runner=.venv/bin/python" in command
    assert '"$runner" -m pytest -q' in command
    assert "npm test" in command


def test_tui_host_shell_test_content_never_assumes_odysseus_test_paths():
    broad = json.loads(_tui_local_test_runner_host_shell_content("Run the tests"))
    smoke = json.loads(_tui_local_test_runner_host_shell_content("Run a quick smoke test"))
    full = json.loads(_tui_local_test_runner_host_shell_content("Run the full test suite"))

    assert _tui_explicit_full_test_request("Run the full test suite")
    assert not _tui_explicit_full_test_request("Run the tests")
    assert "tests/test_tui_round_cap.py" not in broad["command"]
    assert "tests/test_tui_round_cap.py" not in smoke["command"]
    assert "tests/test_tui_round_cap.py" not in full["command"]
    assert broad["command"] == smoke["command"]
    assert broad["command"] != full["command"]
    assert "git diff --name-only" in broad["command"]
    assert "git diff --name-only" not in full["command"]
    assert broad["timeout"] == 120
    assert smoke["timeout"] == 120
    assert full["timeout"] == 120


def test_tui_pytest_normalizer_preserves_target_and_uses_shared_runner():
    command = _tui_normalize_pytest_command(
        "python3 -m pytest -q tests/test_client.py -k nested_error"
    )

    assert command is not None
    assert '"$runner" -m pytest -q tests/test_client.py -k nested_error' in command
    assert "git rev-parse --path-format=absolute --git-common-dir" in command
    assert _tui_normalize_pytest_command("pytest -q | tee results.txt") is None

def test_bash_block_request_gets_a_generic_workspace_probe():
    command = _tui_local_fallback_shell_command("Do a bash block.")
    assert command == "pwd; whoami; uname -srm"


def test_filename_edit_in_explicit_workspace_is_coding_request():
    assert _looks_like_workspace_coding_request(
        'Edit fixture.py in the active workspace: change VALUE from "before" to "after".'
    )


def test_coding_project_request_gets_a_bounded_workspace_probe():
    command = _tui_local_fallback_shell_command("I want to work on the Odysseus tui")
    assert command is not None
    assert 'workspace=$PWD' in command
    assert "git rev-parse --show-toplevel" in command
    assert "git_roots:" in command
    assert "ls -la" in command
    assert "find /" not in command


def test_invalid_backend_workspace_tools_collapse_to_one_host_action():
    blocks, recovered = _tui_recover_invalid_local_tools(
        [ToolBlock("get_workspace", ""), ToolBlock("ls", '{"path":"/app/data"}')],
        "I want to work on the Odysseus tui",
    )
    assert recovered is True
    assert [block.tool_type for block in blocks] == ["host_shell"]
    assert "workspace=$PWD" in blocks[0].content
    assert "/app/data" not in blocks[0].content


def test_host_bridge_coding_rules_do_not_conflict_with_backend_workspace_rules():
    rules = _workspace_coding_rules("/host/home/project", host_bridge=True)
    assert "host bridge owns" in rules
    assert "do not call backend `get_workspace`" in rules
    assert "Start by orienting with `get_workspace`" not in rules


def test_qwen_transport_markers_do_not_leak_into_tui_answer():
    assert _normalize_ody_qwen_text_artifacts(
        "|start|\nThe local IP for ajax is 192.168.1.20.\n|end|"
    ) == "The local IP for ajax is 192.168.1.20."


def test_network_summary_distinguishes_tailscale_from_lan_address():
    assert _tui_network_summary(
        "100.67.207.85 ajax.tail.example\n"
        "5: wlan0 inet 192.168.1.8/24 scope global\n",
        "ajax",
    ) == (
        "ajax resolves to `100.67.207.85`.\n"
        "That is a Tailscale address, not a LAN address.\n"
        "This host's LAN address is `192.168.1.8`."
    )


def test_project_discovery_fallback_lists_real_project_markers_not_home_noise():
    command = _tui_local_fallback_shell_command(
        "Search my computer for the local project I was working on"
    )
    assert command is not None
    assert "git_roots:" in command
    assert "project_manifests:" in command
    assert "node_modules" in command
    assert "ls -la" not in command


def test_project_discovery_summary_is_bounded_and_uses_only_git_roots():
    summary = _tui_project_discovery_summary(
        "workspace=/home/user\n"
        "git_roots:\n./project-a\n./project-a\n./project-b\n"
        "project_manifests:\n./project-a/pyproject.toml\n"
    )
    assert summary == (
        "Projects found in the active workspace:\n"
        "- ./project-a\n"
        "- ./project-b\n"
        "Select a project path and I can inspect its files or run its tests."
    )


def test_model_listing_has_a_deterministic_terminal_summary():
    assert _ody_qwen_terminal_tool_summary({
        "tool": "list_models",
        "command": "",
        "output": "Models:\n- qwen\n- gpt",
    }) == "Models:\n- qwen\n- gpt"


def test_model_listing_summary_is_bounded():
    output = "Available models (100 total):\n" + "\n".join(f"- model-{i}" for i in range(100))
    summary = _ody_qwen_terminal_tool_summary({
        "tool": "list_models",
        "command": "",
        "output": output,
    })
    assert "more models omitted" in summary
    assert len(summary.splitlines()) == 25


def test_network_prompt_routes_away_from_container_shell():
    routed = _route_tui_local_workspace_tools(
        {"bash", "web_search"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
        text="Find the local IP to SSH into ajax.",
        workspace=None,
    )
    assert routed is not None
    assert "host_shell" in routed
    assert "bash" not in routed


def test_local_only_prompt_gets_a_small_tool_surface():
    routed = _route_tui_local_workspace_tools(
        {"bash", "web_search", "web_fetch", "manage_memory", "mcp__builtin_browser__browser_click"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
        text="Inspect this local project using local tools only.",
        workspace=None,
    )
    assert routed == {
        "host_shell", "ask_user", "update_plan", "grep", "ls", "glob", "read_file",
    }


def test_named_private_repo_discovery_stays_on_tui_host_surface():
    prompt = "can you find odysseus private maintainer repo"
    context = _host_bridge_context()

    assert _tui_local_workspace_turn(
        prompt,
        workspace="/home/pewds/odysseus",
        client_runtime_context=context,
    )
    assert _route_tui_local_workspace_tools(
        {"get_workspace", "ls", "host_shell", "web_search"},
        client_runtime_context=context,
        text=prompt,
        workspace="/home/pewds/odysseus",
    ) == {"host_shell", "ask_user", "update_plan"}


def test_bash_block_prompt_stays_on_tui_host_shell_surface():
    routed = _route_tui_local_workspace_tools(
        {"bash", "host_shell", "web_search"},
        client_runtime_context=_host_bridge_context(),
        text="Do a bash block.",
        workspace=None,
    )
    assert routed == {"host_shell", "ask_user", "update_plan"}


def test_tui_session_cwd_test_now_routes_to_host_shell_without_bridge():
    routed = _route_tui_local_workspace_tools(
        {"web_search"},
        client_runtime_context={"surface": "odysseus-tui", "session_cwd": "/tmp/repo"},
        text="test now",
        workspace="/tmp/repo",
    )

    assert routed == {"host_shell", "ask_user", "update_plan"}


def test_tui_session_cwd_personal_request_does_not_route_to_host_shell_without_bridge():
    routed = _route_tui_local_workspace_tools(
        {"manage_notes"},
        client_runtime_context={"surface": "odysseus-tui", "session_cwd": "/tmp/repo"},
        text="What's my notes?",
        workspace="/tmp/repo",
    )

    assert routed == {"manage_notes"}


def test_tui_bridge_general_chat_does_not_route_to_host_shell():
    for prompt in ("Where is Sweden on a map?", "sned links", "hi"):
        routed = _route_tui_local_workspace_tools(
            {"host_shell", "web_search"},
            client_runtime_context=_host_bridge_context(),
            text=prompt,
            workspace=None,
        )

        assert routed == {"host_shell", "web_search"}


def test_local_coding_prompt_exposes_transactional_patch_tools():
    routed = _route_tui_local_workspace_tools(
        {"bash", "apply_patch", "todowrite", "web_search"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
        text="Fix the parser bug in this local project and run the tests.",
        workspace=None,
    )
    assert routed == {
        "host_shell", "ask_user", "update_plan", "read_file", "write_file",
        "apply_patch", "edit_file", "todowrite", "grep", "ls", "glob",
    }


def test_local_coding_prompt_routes_exact_edits_to_the_host_bridge():
    routed = _route_tui_local_workspace_tools(
        {"edit_file", "apply_patch", "todowrite"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
        text="Change status=old to status=new in the file config.txt and verify it.",
        workspace=None,
    )
    assert "edit_file" in routed


def test_explicit_external_lookup_keeps_web_tools():
    routed = _route_tui_local_workspace_tools(
        {"bash", "web_search", "web_fetch"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
        text="Inspect this project, then check the latest GitHub issue online.",
        workspace=None,
    )
    assert {"host_shell", "web_search", "web_fetch"}.issubset(routed)


def test_negated_web_request_stays_local_only():
    routed = _route_tui_local_workspace_tools(
        {"bash", "web_search", "web_fetch"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
        text="Inspect the local project. Do not search the web.",
        workspace=None,
    )
    assert routed == {
        "host_shell", "ask_user", "update_plan", "grep", "ls", "glob", "read_file",
    }


def test_negated_web_phrase_does_not_make_local_project_external():
    prompt = "Search my computer for the local project I was working on. Do not use the web."
    assert not _looks_like_explicit_tui_app_or_external_request(prompt)
    assert _tui_local_workspace_turn(
        prompt,
        workspace=None,
        client_runtime_context=_host_bridge_context(),
    )


def test_tui_local_turn_is_explicitly_narrowed():
    assert _tui_local_workspace_turn(
        "Find the local IP to SSH into ajax.",
        workspace="/home/tester",
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
    )


def test_local_network_turn_is_not_short_circuited_by_web_disabled_guard():
    context = _host_bridge_context()
    assert not _web_search_unavailable_for_turn(
        {"web", "files"},
        set(WEB_TOOL_NAMES),
        "Find the local IP for ajax; do not use web search.",
        context,
        None,
    )
    assert _web_search_unavailable_for_turn(
        {"web"},
        set(WEB_TOOL_NAMES),
        "Search the web for the latest Qwen release.",
        context,
        None,
    )


def test_local_project_turn_is_not_short_circuited_by_web_disabled_guard():
    assert not _web_search_unavailable_for_turn(
        {"web", "files"},
        {"web_search", "web_fetch"},
        "Find the local Odysseus TUI project; do not search the web.",
        _host_bridge_context(),
        None,
    )


def _host_bridge_context():
    return {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project",
        "host_shell_bridge": {"url": "http://host.docker.internal:17654/run"},
        "runtime_execution_contract": {
            "local_workspace_tasks": "use_host_shell_bridge",
        },
    }


def test_active_workspace_does_not_hijack_saved_memory_lookup():
    prompt = "Search saved memory for the Harbor Guji delay."
    assert _looks_like_explicit_tui_personal_domain_request(prompt)
    assert not _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )
    assert _route_tui_local_workspace_tools(
        {"host_shell", "manage_memory", "search_chats"},
        client_runtime_context=_host_bridge_context(),
        text=prompt,
        workspace="/home/tester/project",
    ) == {"host_shell", "manage_memory", "search_chats"}


def test_active_workspace_does_not_hijack_task_list():
    prompt = "List my tasks."
    assert _looks_like_explicit_tui_personal_domain_request(prompt)
    assert not _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )


def test_active_workspace_does_not_hijack_chat_session_list():
    prompt = "List my chat sessions."
    assert _looks_like_explicit_tui_personal_domain_request(prompt)
    assert not _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )


def test_rejected_backend_workspace_does_not_hijack_saved_memory_lookup():
    prompt = "Search saved memory for the Harbor Guji delay. Do not use the workspace."
    assert not _tui_local_workspace_turn(
        prompt,
        workspace=None,
        client_runtime_context=_host_bridge_context(),
    )


def test_active_workspace_does_not_hijack_prior_chat_search():
    prompt = "Find the prior chat where we fixed the parser."
    assert _looks_like_explicit_tui_personal_domain_request(prompt)
    assert not _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )


def test_code_file_named_notes_stays_on_host_workspace():
    prompt = "Fix the parser bug in notes.py and run the tests."
    assert not _looks_like_explicit_tui_personal_domain_request(prompt)
    assert _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )


def test_active_workspace_does_not_hijack_model_picker_or_cookbook_requests():
    for prompt in (
        "What models are running on Odysseus?",
        "Switch to the Qwen model.",
    ):
        assert _looks_like_explicit_tui_app_or_external_request(prompt)
        assert not _tui_local_workspace_turn(
            prompt,
            workspace="/home/tester/project",
            client_runtime_context=_host_bridge_context(),
        )


def test_model_registry_requests_are_distinct_from_model_switches():
    for prompt in (
        "What models are running on the server?",
        "List the available models.",
        "Show served models.",
    ):
        assert _is_qwen_explicit_model_list_request(prompt), prompt
    for prompt in ("Switch to the Qwen model.", "Use the model qwen35."):
        assert not _is_qwen_explicit_model_list_request(prompt), prompt


def test_latest_email_request_is_singular_but_email_search_is_not():
    for prompt in ("What's my latest email?", "Show the newest mail."):
        assert _is_qwen_explicit_latest_email_request(prompt), prompt
    for prompt in ("Find emails from Runpod", "Search my inbox for messages about invoices"):
        assert not _is_qwen_explicit_latest_email_request(prompt), prompt


def test_active_workspace_does_not_hijack_explicit_web_search():
    prompt = "Search the web for the latest Qwen release."
    assert _looks_like_explicit_tui_app_or_external_request(prompt)
    assert not _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )


def test_tdd_skill_plus_parser_task_stays_on_host_workspace():
    prompt = "Use the TDD skill to fix the parser and run tests."
    assert not _looks_like_explicit_tui_personal_domain_request(prompt)
    assert _tui_local_workspace_turn(
        prompt,
        workspace="/home/tester/project",
        client_runtime_context=_host_bridge_context(),
    )
    routed = _route_tui_local_workspace_tools(
        {"host_shell", "manage_skills", "apply_patch", "todowrite"},
        client_runtime_context=_host_bridge_context(),
        text=prompt,
        workspace="/home/tester/project",
    )
    assert "host_shell" in routed
    assert "manage_skills" in routed
