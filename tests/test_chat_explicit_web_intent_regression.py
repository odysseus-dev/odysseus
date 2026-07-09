"""Regression guard for chat_stream web-intent routing state."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_explicit_web_intent_is_defined_before_use():
    tree = ast.parse((ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8"))
    chat_stream = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream"
    )

    first_store = None
    first_load = None
    for node in ast.walk(chat_stream):
        if not isinstance(node, ast.Name) or node.id != "_explicit_web_intent":
            continue
        if isinstance(node.ctx, ast.Store):
            first_store = node.lineno if first_store is None else min(first_store, node.lineno)
        elif isinstance(node.ctx, ast.Load):
            first_load = node.lineno if first_load is None else min(first_load, node.lineno)

    assert first_store is not None
    assert first_load is not None
    assert first_store < first_load
