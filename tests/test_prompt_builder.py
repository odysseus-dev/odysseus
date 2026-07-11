"""Tests for src/agent/prompt_builder.py"""
from __future__ import annotations
from src.agent.prompt_builder import PromptBuilder, PromptSection


def test_prompt_section_dataclass():
    section = PromptSection(id="base", content="You are an AI assistant.", priority=100, trusted=True)
    assert section.id == "base"
    assert section.trusted is True


def test_prompt_builder_add_section():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Base prompt.", priority=100, trusted=True))
    builder.add_section(PromptSection(id="tools", content="Tools available: bash.", priority=90, trusted=True))
    prompt = builder.build()
    assert "Base prompt." in prompt
    assert "Tools available: bash." in prompt


def test_prompt_builder_priority_order():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="low", content="LOW", priority=10, trusted=True))
    builder.add_section(PromptSection(id="high", content="HIGH", priority=100, trusted=True))
    prompt = builder.build()
    assert prompt.index("HIGH") < prompt.index("LOW")


def test_prompt_builder_excludes_disabled():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="web", content="Web search tool.", priority=80, trusted=True))
    builder.disable_tools({"web_search"})
    prompt = builder.build()
    assert "Web search tool." not in prompt


def test_prompt_builder_untrusted_not_in_system():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Base.", priority=100, trusted=True))
    builder.add_section(PromptSection(id="user_ctx", content="User context.", priority=50, trusted=False))
    system_prompt, untrusted = builder.build_with_untrusted()
    assert "Base." in system_prompt
    assert "User context." not in system_prompt
    assert any("User context." in u.get("content", "") for u in untrusted)


def test_prompt_builder_domain_sections():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Base.", priority=100, trusted=True))
    builder.add_domain_rule("web", "Web rules: search first.")
    builder.add_domain_rule("email", "Email rules: use email tools.")
    builder.set_relevant_tools({"web_search", "web_fetch"})
    system_prompt, _ = builder.build_with_untrusted()
    assert "Web rules" in system_prompt
    assert "Email rules" not in system_prompt


def test_prompt_builder_compact_mode():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Full prompt with details.", priority=100, trusted=True))
    builder.set_compact(True)
    prompt = builder.build()
    assert isinstance(prompt, str)