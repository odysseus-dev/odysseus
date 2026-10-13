from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compact_router_keeps_basic_agent_tools_available():
    source = (ROOT / "src/agent_loop.py").read_text()
    assert '_core_agent_tools = set(CONTRACT_CORE_TOOLS)' in source
    assert "_relevant_tools.update(_core_agent_tools)" in source
    assert "_base_relevant_tools.update(_core_agent_tools)" in source
    assert "_caller_disabled_tools" in source


def test_caller_disabled_tools_are_removed_and_execution_blocked():
    from src.agent_loop import _enforce_caller_disabled_tool_policy

    disabled = set()
    relevant, base, policy = _enforce_caller_disabled_tool_policy(
        {"manage_calendar"},
        disabled,
        {"bash", "manage_calendar"},
        {"host_shell", "manage_calendar"},
        None,
    )

    assert disabled == {"manage_calendar"}
    assert relevant == {"bash"}
    assert base == {"host_shell"}
    assert policy is not None
    assert policy.blocks("manage_calendar") is True


def test_named_ssh_turn_also_exposes_cookbook_server_resolution():
    source = (ROOT / "src/agent_loop.py").read_text()
    assert 're.search(r"\\bssh\\s+' in source
    assert '_relevant_tools.add("list_cookbook_servers")' in source
    assert '_base_relevant_tools.add("list_cookbook_servers")' in source


def test_sft_fixture_keeps_read_only_bash_but_not_workspace_mutators():
    from src.agent_loop import _strip_workspace_tools_for_sft

    selected = _strip_workspace_tools_for_sft(
        {"bash", "read_file", "write_file", "edit_file", "manage_skills"},
        "sft_alex_creator",
    )
    assert "bash" in selected
    assert "manage_skills" in selected
    assert not ({"read_file", "write_file", "edit_file"} & selected)


def test_cookbook_intent_is_not_replaced_by_workspace_terminus_tools():
    source = (ROOT / "src/agent_loop.py").read_text()

    assert 'and "cookbook" not in (_intent.get("domains") or set())' in source


def test_incomplete_shell_heredoc_is_not_treated_as_success():
    from src.agent_loop import _normalize_incomplete_shell_artifact_result

    original = {
        "output": (
            "bash: line 58: warning: here-document at line 1 delimited "
            "by end-of-file (wanted `EOF')\n"
        ),
        "exit_code": 0,
    }
    normalized = _normalize_incomplete_shell_artifact_result(
        "bash",
        "cat > report.md << 'EOF'\npartial",
        original,
    )

    assert original["exit_code"] == 0
    assert normalized["exit_code"] == 1
    assert "append only the missing content" in normalized["error"]


def test_complete_or_non_shell_commands_are_not_reclassified():
    from src.agent_loop import _normalize_incomplete_shell_artifact_result

    successful = {"output": "written", "exit_code": 0}

    assert _normalize_incomplete_shell_artifact_result(
        "bash", "printf ok > report.md", successful
    ) is successful
    assert _normalize_incomplete_shell_artifact_result(
        "write_file", "report.md\ncontent", successful
    ) is successful
