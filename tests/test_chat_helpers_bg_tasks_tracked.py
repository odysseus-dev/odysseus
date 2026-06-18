"""Guard: chat hot-path background tasks must go through _spawn_bg.

asyncio only holds a weak reference to a bare create_task() result, so the
GC can collect the outer task before its body runs and the background work
(memory/skill extraction, session auto-naming) silently never happens.
routes/chat_helpers.py owns these schedules via _spawn_bg(), which adds the
task to _BG_TASKS and discards it via a done-callback. This guard catches a
regression where a copy-paste re-introduces a bare asyncio.create_task.

This is the routes/chat_helpers.py-scoped sibling of the webhook-emitter
guard added in #4336 (tests/test_webhook_emitters_use_manager.py).
"""
import ast
from pathlib import Path

CHAT_HELPERS = (
    Path(__file__).resolve().parent.parent / "routes" / "chat_helpers.py"
)


def _untracked_create_task_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, snippet) for any bare asyncio.create_task(...).

    A call is "bare" when its return value is dropped — i.e. it is the direct
    expression of an ast.Expr statement. Captured forms (`x = asyncio.create_task(...)`,
    `[asyncio.create_task(...), ...]`, `await asyncio.create_task(...)`) are fine
    because something else holds the reference.

    The helper itself (_spawn_bg) is exempt: it calls asyncio.create_task once
    and registers the task in _BG_TASKS before returning.
    """
    hits: list[tuple[int, str]] = []

    def _is_create_task(call: ast.Call) -> bool:
        f = call.func
        return (
            isinstance(f, ast.Attribute)
            and f.attr == "create_task"
            and isinstance(f.value, ast.Name)
            and f.value.id == "asyncio"
        )

    spawn_helper_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_spawn_bg":
            for n in ast.walk(node):
                if hasattr(n, "lineno"):
                    spawn_helper_lines.add(n.lineno)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if not _is_create_task(node.value):
            continue
        if node.lineno in spawn_helper_lines:
            continue
        hits.append((node.lineno, ast.unparse(node.value)))
    return hits


def test_no_untracked_create_task_in_chat_helpers():
    tree = ast.parse(CHAT_HELPERS.read_text(), filename=str(CHAT_HELPERS))
    offenders = _untracked_create_task_calls(tree)
    assert not offenders, (
        "Background tasks scheduled from routes/chat_helpers.py must go through "
        "_spawn_bg(coro) so the task is registered in _BG_TASKS and survives until "
        "it finishes. Found bare asyncio.create_task(...) call(s):\n  "
        + "\n  ".join(f"chat_helpers.py:{ln}: {snip}" for ln, snip in offenders)
    )
