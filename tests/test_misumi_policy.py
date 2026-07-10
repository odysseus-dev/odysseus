from src.misumi_policy import (
    normalize_persona,
    persona_disabled_tools,
    persona_record,
    policy_summary,
)
from src.tool_policy import build_effective_tool_policy


def test_unknown_persona_falls_back_to_aoteru():
    assert normalize_persona("unknown") == "aoteru"


def test_kurisu_categories_do_not_include_cooking():
    record = persona_record("kurisu")
    assert "cooking" not in record["allowed_skill_categories"]
    assert "evidence" in record["allowed_skill_categories"]


def test_jin_cannot_use_shell_even_with_execution_approval():
    disabled = persona_disabled_tools("jin", "approved_execute")
    assert {"bash", "python"}.issubset(disabled)


def test_lelouch_shell_requires_explicit_execution_approval():
    assert "bash" in persona_disabled_tools("lelouch", "plan_only")
    assert "bash" not in persona_disabled_tools("lelouch", "approved_execute")


def test_aoteru_does_not_bypass_policy():
    policy = build_effective_tool_policy(persona="aoteru", approval="approved_execute")
    assert policy.blocks("send_email")
    assert policy.blocks("bash")


def test_policy_summary_never_allows_household_writes_in_phase_a():
    summary = policy_summary("lelouch", "approved_execute")
    assert summary["writes_allowed"] is False
