"""Regression: the scheduled-research settings reads in task_scheduler.py must
not crash when settings.json holds a non-numeric value.

`int(get_setting("research_max_tokens", 8192))` ran with no error handling on a
code path that has no surrounding exception handler, so a hand-edited or
agent-written data/settings.json (e.g. {"research_max_tokens": "invalid"}) made
int() raise ValueError and broke every scheduled research task. research_handler.py
already guards the same reads; the scheduler must too.
"""
import ast
from pathlib import Path

import pytest

_TASK_SCHEDULER = Path(__file__).resolve().parent.parent / "src" / "task_scheduler.py"


def _research_read_is_guarded(source: str) -> bool:
    """True if a `try` that reads get_setting("research_max_tokens") catches ValueError."""
    tree = ast.parse(source)
    for try_node in ast.walk(tree):
        if not isinstance(try_node, ast.Try):
            continue
        reads_research = any(
            isinstance(call, ast.Call)
            and getattr(call.func, "id", None) == "get_setting"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == "research_max_tokens"
            for stmt in try_node.body
            for call in ast.walk(stmt)
        )
        if not reads_research:
            continue
        catches_value_error = any(
            (isinstance(h.type, ast.Name) and h.type.id == "ValueError")
            or (isinstance(h.type, ast.Tuple)
                and any(isinstance(e, ast.Name) and e.id == "ValueError" for e in h.type.elts))
            for h in try_node.handlers
        )
        if catches_value_error:
            return True
    return False


def test_research_max_tokens_read_is_guarded():
    source = _TASK_SCHEDULER.read_text(encoding="utf-8")
    assert _research_read_is_guarded(source), (
        "int(get_setting('research_max_tokens', ...)) in task_scheduler.py must be "
        "wrapped in try/except (ValueError) so a non-numeric settings.json value "
        "cannot crash the scheduled research task"
    )


@pytest.mark.parametrize("raw, expected", [
    ("invalid", 8192), ("", 8192), (None, 8192), ("4096", 4096), (2048, 2048),
])
def test_research_max_tokens_coercion_falls_back_to_default(raw, expected):
    # Mirrors the guarded read: a bad/non-numeric value -> default 8192.
    def get_setting(_key, default):
        return raw if raw is not None else default

    try:
        max_tokens = int(get_setting("research_max_tokens", 8192))
    except (TypeError, ValueError):
        max_tokens = 8192
    assert max_tokens == expected
