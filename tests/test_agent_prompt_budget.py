import src.agent_loop as agent_loop


class _FakeMcpManager:
    def get_tool_descriptions_for_prompt(self, disabled_map=None):
        return "\n\nPRIVATE_MCP_DESCRIPTION"


def test_assemble_prompt_sections_preserve_joined_prompt():
    tools = {"bash", "python", "manage_memory", "list_sessions"}
    disabled = {"python"}

    sections = agent_loop._assemble_prompt_sections(tools, disabled_tools=disabled)

    assert agent_loop._join_prompt_sections(sections) == agent_loop._assemble_prompt(
        tools,
        disabled_tools=disabled,
    )
    categories_by_name = {section.name: section.category for section in sections}
    assert categories_by_name["builtin_tool.manage_memory"] == "memory_tool"
    assert "builtin_tool.one_liners_header" in categories_by_name
    assert all("python" not in section.name for section in sections)


def test_compact_prompt_sections_include_selected_tool_count():
    sections = agent_loop._assemble_prompt_sections(
        {"bash", "manage_memory"},
        compact=True,
    )

    tool_section = next(section for section in sections if section.name == "available_tool_names")
    assert tool_section.category == "tool_selection"
    assert tool_section.item_count == 2
    assert "bash, manage_memory" in tool_section.text


def test_base_prompt_budget_report_covers_dynamic_sections_without_text_leak(monkeypatch):
    import src.integrations as integrations

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(
        agent_loop,
        "_build_skill_index_block",
        lambda disabled: "\n\nPRIVATE_SKILL_INDEX",
    )
    monkeypatch.setattr(
        integrations,
        "get_integrations_prompt",
        lambda: "PRIVATE_INTEGRATION_DESCRIPTION",
    )

    report = agent_loop.build_base_prompt_budget_report(
        disabled_tools=set(),
        mcp_mgr=_FakeMcpManager(),
        needs_admin=False,
        relevant_tools={"manage_memory", "bash"},
    )

    rows = {row["name"]: row for row in report["sections"]}
    assert rows["builtin_tool.manage_memory"]["category"] == "memory_tool"
    assert rows["mcp_tool_descriptions"]["category"] == "mcp"
    assert rows["integration_descriptions"]["category"] == "integration"
    assert rows["skill_index"]["category"] == "skill_context"
    assert report["largest"] == sorted(
        report["largest"],
        key=lambda row: (
            -row["estimated_tokens"],
            -row["char_count"],
            row["category"],
            row["name"],
        ),
    )

    rendered = str(report)
    assert "PRIVATE_MCP_DESCRIPTION" not in rendered
    assert "PRIVATE_INTEGRATION_DESCRIPTION" not in rendered
    assert "PRIVATE_SKILL_INDEX" not in rendered


def test_base_prompt_preserves_mcp_leading_newline_behavior(monkeypatch):
    import src.integrations as integrations

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "_build_skill_index_block", lambda disabled: "")
    monkeypatch.setattr(integrations, "get_integrations_prompt", lambda: "INTEGRATION")

    prompt, skill_index = agent_loop._build_base_prompt(
        disabled_tools=set(),
        mcp_mgr=_FakeMcpManager(),
        needs_admin=False,
        relevant_tools={"bash"},
    )

    assert skill_index == ""
    assert "\n\nINTEGRATION\n\nPRIVATE_MCP_DESCRIPTION" in prompt
    assert "\n\nINTEGRATION\n\n\n\nPRIVATE_MCP_DESCRIPTION" not in prompt
