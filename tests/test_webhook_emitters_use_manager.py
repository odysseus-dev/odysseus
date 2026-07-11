"""Guard: every public webhook emitter goes through the manager.

Public emitters in `routes/` must schedule their fire through
`webhook_manager.fire_and_forget(...)` (or `_spawn_tracked`). A bare
`asyncio.create_task(webhook_manager.fire(...))` escapes
`WebhookManager._bg_tasks`, so asyncio only holds a weak reference to the
delivery task and the GC can collect it before it sends — silently dropping
the webhook. Catching this with a scan stops a regression from sneaking
back in via a copy-paste.
"""
import ast
from pathlib import Path

ROUTES_DIR = Path(__file__).resolve().parent.parent / "routes"


def _untracked_fire_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, snippet) for any asyncio.create_task(webhook_manager.fire(...))."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create_task"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "asyncio"):
            continue
        if not node.args:
            continue
        inner = node.args[0]
        if not isinstance(inner, ast.Call):
            continue
        inner_func = inner.func
        if (
            isinstance(inner_func, ast.Attribute)
            and inner_func.attr == "fire"
            and isinstance(inner_func.value, ast.Name)
            and inner_func.value.id == "webhook_manager"
        ):
            hits.append((node.lineno, ast.unparse(node)))
    return hits


def test_no_untracked_webhook_fire_in_routes():
    offenders: list[str] = []
    for path in ROUTES_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno, snippet in _untracked_fire_calls(tree):
            offenders.append(f"{path.relative_to(ROUTES_DIR.parent)}:{lineno}: {snippet}")
    assert not offenders, (
        "Public webhook emitters must use webhook_manager.fire_and_forget(...) "
        "so the delivery task is tracked in WebhookManager._bg_tasks. Found "
        "untracked emitter(s):\n  " + "\n  ".join(offenders)
    )
