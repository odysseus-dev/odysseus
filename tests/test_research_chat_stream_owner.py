"""Verify that research launched from the chat stream passes owner to start_research."""

import ast
import textwrap
from pathlib import Path

_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


def test_chat_stream_start_research_passes_owner():
    """The start_research call in the chat-stream path must include owner=<user>."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find all calls to *.start_research or start_research
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == "start_research":
            calls.append(node)

    assert calls, "No start_research calls found in chat_routes.py"

    for call in calls:
        kwarg_names = [kw.arg for kw in call.keywords]
        assert "owner" in kwarg_names, (
            f"start_research call at line {call.lineno} is missing owner= keyword argument"
        )
