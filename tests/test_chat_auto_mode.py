"""Auto mode — full autonomous agent loop from the chat composer.

Auto mode makes Odysseus power through a task end-to-end without stopping:
  1. It is read from form_data with a JSON-body fallback (API callers).
  2. It forces agent mode (full tools), not chat mode.
  3. It drops the ask_user tool so the agent can't stall waiting on a button.
  4. It ignores guide-only phrasing (empty last_user_message to the policy).
  5. It raises the caps: unlimited tool calls + a high round floor (<=200).

The chat_stream handler is large and app-coupled, so we assert behavior with
AST/source-level guards (robust to line shifts) plus a functional replication
of the cap logic — the same approach as test_chat_route_tool_policy.py.
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CHAT_ROUTES = _ROOT / "routes" / "chat_routes.py"
_CHAT_JS = _ROOT / "static" / "js" / "chat.js"
_INDEX = _ROOT / "static" / "index.html"


def _chat_stream_func():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            return node, source
    raise AssertionError("chat_stream function not found")


# ── Source-level guards ─────────────────────────────────────────


def test_auto_mode_read_with_body_fallback():
    """auto_mode must be parsed from form_data with a JSON-body fallback."""
    func, source = _chat_stream_func()
    seg = None
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "auto_mode":
                    seg = ast.get_source_segment(source, node)
    assert seg is not None, "chat_stream must assign auto_mode"
    assert "form_data" in seg and "body" in seg, (
        "auto_mode must read from form_data with a JSON-body fallback"
    )
    assert '"true"' in seg or "'true'" in seg, "auto_mode must be a truthy-string check"


def test_auto_mode_forces_agent_mode():
    """auto_mode must force chat_mode to agent (full tools)."""
    _, source = _chat_stream_func()
    assert "if auto_mode:" in source
    # The auto block forces agent mode.
    idx = source.index("if auto_mode:")
    window = source[idx: idx + 400]
    assert 'chat_mode = "agent"' in window, "auto_mode must force chat_mode='agent'"


def test_auto_mode_disables_ask_user():
    """auto_mode must disable ask_user so the agent never pauses for a button."""
    _, source = _chat_stream_func()
    assert 'disabled_tools.add("ask_user")' in source, (
        "auto_mode must add ask_user to disabled_tools"
    )


def test_auto_mode_bypasses_guide_only():
    """auto_mode must pass an empty last_user_message so guide-only can't disarm it."""
    _, source = _chat_stream_func()
    assert 'last_user_message="" if auto_mode else message' in source, (
        "build_effective_tool_policy must ignore guide-only phrasing under auto_mode"
    )


def test_auto_mode_raises_caps():
    """auto_mode must lift the tool budget and round floor."""
    _, source = _chat_stream_func()
    idx = source.rindex("if auto_mode:")
    window = source[idx: idx + 300]
    assert "_tool_budget = 0" in window, "auto_mode must set unlimited tool calls"
    assert "_max_rounds" in window and "100" in window, (
        "auto_mode must raise the round floor"
    )


# ── Functional replication of the cap logic ─────────────────────


def _auto_caps(max_rounds_setting, tool_budget_setting, auto):
    """Replicate the auto-mode cap math from chat_stream."""
    tool_budget = int(tool_budget_setting)
    max_rounds = max(1, min(int(max_rounds_setting), 200))
    if auto:
        tool_budget = 0
        max_rounds = min(200, max(max_rounds, 100))
    return max_rounds, tool_budget


def test_auto_lifts_low_round_setting_to_floor():
    rounds, budget = _auto_caps(max_rounds_setting=20, tool_budget_setting=50, auto=True)
    assert rounds == 100, "a low rounds setting must be lifted to the 100 floor"
    assert budget == 0, "auto must make tool calls unlimited"


def test_auto_keeps_higher_setting_under_cap():
    rounds, _ = _auto_caps(max_rounds_setting=150, tool_budget_setting=0, auto=True)
    assert rounds == 150, "a higher setting must be preserved"


def test_auto_never_exceeds_hard_cap():
    rounds, _ = _auto_caps(max_rounds_setting=999, tool_budget_setting=0, auto=True)
    assert rounds == 200, "auto must still honor the 200 hard cap"


def test_non_auto_leaves_caps_untouched():
    rounds, budget = _auto_caps(max_rounds_setting=20, tool_budget_setting=50, auto=False)
    assert rounds == 20 and budget == 50, "without auto, the configured caps stand"


# ── Frontend guards ─────────────────────────────────────────────


def test_frontend_sends_auto_field():
    """chat.js must send the auto flag to the stream endpoint."""
    source = _CHAT_JS.read_text(encoding="utf-8")
    assert "'auto'" in source or '"auto"' in source, (
        "chat.js must append the auto field to the chat_stream FormData"
    )


def test_ui_has_auto_toggle():
    """index.html must expose an auto-mode control."""
    source = _INDEX.read_text(encoding="utf-8")
    assert "auto-toggle" in source, "index.html must contain the auto-mode toggle"
