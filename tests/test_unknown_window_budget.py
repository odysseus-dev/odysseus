"""The trim budget must leave room for the conversation.

Seen live on `gpt-6-astra` (ChatGPT Subscription / Codex): the window read as
unknown, the agent budget fell to the 6000 default, and ~7100 tokens of output
reserve + tool schemas were subtracted from it — `Trimming messages: 34289
tokens > -1104 budget (ctx=6000)`. With a negative budget nothing ever fit, so
every round took the harshest trim: the TODO.md it had just read was cut out,
the user's feedback shrank to a fragment, and the agent asked "What would you
like me to work on next?".
"""

from src.context_budget import DEFAULT_BUDGET, compute_input_token_budget
from src.context_compactor import trim_for_context
from src.model_context import estimate_tokens


def _agent_run():
    msgs = [
        {"role": "system", "content": "You are Astra. " + "Guidance. " * 800},
        {"role": "user", "content": "Feedback for the dog trainer MVP: " + "requirement. " * 400},
        {"role": "assistant", "content": "On it."},
        {"role": "user", "content": "Use available skills to finish the dog trainer app"},
    ]
    return msgs


def test_unknown_window_budget_covers_the_reserve():
    reserve = 7104
    budget = compute_input_token_budget(DEFAULT_BUDGET, 0, explicit=False, reserve=reserve)
    assert budget - reserve == DEFAULT_BUDGET


def test_reserve_does_not_change_known_or_explicit_budgets():
    assert compute_input_token_budget(DEFAULT_BUDGET, 128000, explicit=False, reserve=7104) == int(128000 * 0.85)
    assert compute_input_token_budget(20000, 0, explicit=True, reserve=7104) == 20000


def test_reserve_larger_than_budget_never_goes_negative(caplog):
    caplog.set_level("INFO", logger="src.context_compactor")
    trimmed = trim_for_context(_agent_run(), 6000, reserve_tokens=7104)
    assert "-1104" not in caplog.text
    assert "budget (ctx=6000)" in caplog.text
    # Half the window stays available to messages, so the latest request and
    # the conversation survive instead of being reduced to fragments.
    assert trimmed[-1]["content"] == "Use available skills to finish the dog trainer app"
    assert estimate_tokens(trimmed) > 1000
