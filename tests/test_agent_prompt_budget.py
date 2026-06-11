import src.agent_loop as agent_loop


class _FakeMcpManager:
    def get_tool_descriptions_for_prompt(self, disabled_map=None):
        return "\n\nPRIVATE_MCP_DESCRIPTION"


def test_assemble_prompt_sections_preserve_joined_prompt():
    tools = {"bash", "python", "manage_memory", "list_sessions"}
    disabled = {"python"}

    sections = agent_loop._assemble_prompt_sections(tools, disabled)

    categories_by_name = {section.name: section.category for section in sections}
    assert categories_by_name["tool_manage_memory"] == "tools"
    assert "tool_bash" in categories_by_name
    assert all("python" not in section.name for section in sections)


def test_compact_prompt_sections_include_selected_tool_count():
    sections = agent_loop._assemble_prompt_sections(
        {"bash", "manage_memory"},
        set(),
        compact=True,
    )

    assert len(sections) == 2
    names = {s.name for s in sections}
    assert names == {"tool_bash", "tool_manage_memory"}


def test_base_prompt_budget_report_covers_dynamic_sections_without_text_leak(monkeypatch):
    import src.integrations as integrations

    from src.prompt_budget import build_prompt_budget_report

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(
        integrations,
        "get_integrations_prompt",
        lambda: "PRIVATE_INTEGRATION_DESCRIPTION",
    )

    sections = agent_loop._build_base_prompt_sections(
        disabled_tools=set(),
        mcp_mgr=_FakeMcpManager(),
        needs_admin=False,
        relevant_tools={"manage_memory", "bash"},
    )
    report = build_prompt_budget_report(sections)

    rows = {row["name"]: row for row in report["sections"]}
    assert rows["tool_manage_memory"]["category"] == "tools"
    assert rows["mcp_tool_descriptions"]["category"] == "mcp"
    assert rows["integration_descriptions"]["category"] == "integration"
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


def test_base_prompt_preserves_mcp_leading_newline_behavior(monkeypatch):
    import src.integrations as integrations

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(integrations, "get_integrations_prompt", lambda: "INTEGRATION")

    prompt, skill_index = agent_loop._build_base_prompt(
        disabled_tools=set(),
        mcp_mgr=_FakeMcpManager(),
        needs_admin=False,
        relevant_tools={"bash"},
    )

    assert skill_index == ""
    assert "\n\nINTEGRATION\n\nPRIVATE_MCP_DESCRIPTION" in prompt
