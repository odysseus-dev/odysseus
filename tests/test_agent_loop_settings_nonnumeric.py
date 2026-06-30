"""Regression: agent_loop.py numeric settings reads must tolerate non-numeric values.

- agent_stream_timeout_seconds was read with int(get_setting(...)) on a code path
  with NO surrounding handler, so a hand-edited/agent-written settings.json with a
  non-numeric value crashed the agent loop with ValueError.
- agent_input_token_budget had the same int() read; it was caught by a broad outer
  `except Exception` (silent degradation), inconsistent with the explicit
  agent_input_token_hard_max guard right below it.

Both reads must now be wrapped in try/except (ValueError) with an explicit fallback.
"""
import ast
from pathlib import Path

import pytest

_AGENT_LOOP = Path(__file__).resolve().parent.parent / "src" / "agent_loop.py"


def _setting_read_guarded(source: str, key: str) -> bool:
    """True if a get_setting(key) read has an enclosing try that catches ValueError."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node

    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Call)
         and getattr(n.func, "id", None) == "get_setting"
         and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == key),
        None,
    )
    assert target is not None, f"get_setting({key!r}) read not found"

    parent = getattr(target, "_parent", None)
    while parent is not None:
        if isinstance(parent, ast.Try):
            for h in parent.handlers:
                names = []
                if isinstance(h.type, ast.Name):
                    names = [h.type.id]
                elif isinstance(h.type, ast.Tuple):
                    names = [getattr(e, "id", None) for e in h.type.elts]
                if "ValueError" in names:
                    return True
        parent = getattr(parent, "_parent", None)
    return False


@pytest.mark.parametrize("key", ["agent_stream_timeout_seconds", "agent_input_token_budget"])
def test_agent_loop_numeric_setting_read_is_guarded(key):
    source = _AGENT_LOOP.read_text(encoding="utf-8")
    assert _setting_read_guarded(source, key), (
        f"int(get_setting('{key}', ...)) in agent_loop.py must be wrapped in "
        "try/except (ValueError) so a non-numeric settings.json value cannot crash "
        "or silently abort the agent loop"
    )


@pytest.mark.parametrize("raw, default, expected", [
    ("invalid", 300, 300), ("", 300, 300), (None, 300, 300), ("120", 300, 120),
])
def test_stream_timeout_coercion_falls_back_to_default(raw, default, expected):
    def get_setting(_key, d):
        return raw if raw is not None else d

    try:
        timeout = int(get_setting("agent_stream_timeout_seconds", default) or default)
    except (TypeError, ValueError):
        timeout = default
    assert timeout == expected
