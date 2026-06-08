"""Issue #2947 — _truncate_message_to_token_budget must shrink oversized tool_calls
arguments, not just text content.

A tool-only assistant turn persists content=None with its whole payload in
tool_calls[].function.arguments. The text-content truncation can't reach it, so
trim_for_context's last-resort message shrink left the message over budget and the
upstream call 400'd. This pins that oversized args are bounded (so the message
fits) while id/type/function.name are preserved, and that small args / plain text
are untouched.
"""
import json
import sys
from unittest.mock import MagicMock

import pytest

for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.context_compactor import _truncate_message_to_token_budget  # noqa: E402
from src.model_context import estimate_tokens  # noqa: E402


def _tool_msg(arg_len):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "create_document", "arguments": "x" * arg_len},
        }],
    }


def test_oversized_tool_call_args_are_truncated_to_fit_budget():
    budget = 200
    out = _truncate_message_to_token_budget(_tool_msg(40000), budget)
    # The message now fits the budget (before the fix it stayed ~12k tokens).
    assert estimate_tokens([out]) <= budget, estimate_tokens([out])
    tc = out["tool_calls"][0]
    # Structure preserved so tool/result pairing + provider validation still hold.
    assert tc["id"] == "c1" and tc["type"] == "function"
    assert tc["function"]["name"] == "create_document"
    # Arguments remain valid JSON, just bounded.
    parsed = json.loads(tc["function"]["arguments"])
    assert parsed.get("_truncated_for_context") == 40000


def test_small_tool_call_args_are_left_untouched():
    out = _truncate_message_to_token_budget(_tool_msg(20), 500)
    assert out["tool_calls"][0]["function"]["arguments"] == "x" * 20


def test_plain_text_content_still_truncates():
    out = _truncate_message_to_token_budget({"role": "user", "content": "y" * 40000}, 200)
    assert len(out["content"]) < 2000  # truncated, not left at 40k
