"""Novita's /models catalog reports the window as ``context_size``.

Without it the window was "unknown", so the agent's soft input budget fell back
to 6000 tokens and a follow-up turn lost the previous answer.
"""

from src.context_budget import compute_input_token_budget, DEFAULT_BUDGET, DEFAULT_HARD_MAX
from src.model_context import _model_ctx_from_entry


def test_novita_context_size_field_is_read():
    entry = {"id": "deepseek/deepseek-v4.1-flash", "context_size": 1048576}
    assert _model_ctx_from_entry(entry) == 1048576


def test_novita_window_lifts_budget_above_default():
    ctx = _model_ctx_from_entry({"id": "deepseek/deepseek-v4.1-flash", "context_size": 1048576})
    budget = compute_input_token_budget(DEFAULT_BUDGET, ctx, False)
    assert budget == DEFAULT_HARD_MAX > DEFAULT_BUDGET


def test_entry_without_window_still_unknown():
    assert _model_ctx_from_entry({"id": "x", "context_size": 0}) is None
