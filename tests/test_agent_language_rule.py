"""Agent replies (progress notes, plans, answers) follow the user's language.

A Russian request produced English narration and an English plan because the
system prompts are English and never said which language to answer in.
"""
import pytest

agent_loop = pytest.importorskip("src.agent_loop")


def test_plan_mode_directive_requires_user_language():
    d = agent_loop.PLAN_MODE_DIRECTIVE
    assert "LANGUAGE" in d and "Russian" in d


def test_both_agent_prompt_variants_carry_language_rule():
    import inspect
    src = inspect.getsource(agent_loop)
    assert src.count("- LANGUAGE: write everything the user reads") == 2
