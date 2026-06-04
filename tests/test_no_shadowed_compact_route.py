"""Guard for issue #2644 — duplicate, shadowed compact_session route.

Two handlers existed for POST /api/session/{session_id}/compact:
  - routes/session_routes.py  (router prefix "/api", registered first -> live)
  - routes/history_routes.py  (full path, registered later -> dead/shadowed)

FastAPI matches the first-registered route, so the history_routes copy never
ran. The two had already diverged (a None-content guard and an active-run gate
were each applied to the wrong/both copies), so the duplicate was a real
maintenance hazard. This pins the dead copy as removed: the live handler stays
in session_routes, and history_routes must not redefine it.
"""
import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / "routes"
COMPACT_PATH = "/api/session/{session_id}/compact"


def _compact_route_decorators(py_file: Path):
    """Return the list of route paths that register a POST compact handler."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.endswith("/compact"):
                    hits.append((node.name, arg.value))
    return hits


def test_history_routes_does_not_define_compact():
    hits = _compact_route_decorators(ROUTES / "history_routes.py")
    assert hits == [], (
        f"history_routes.py must not redefine the compact route (it is shadowed "
        f"by session_routes); found {hits}"
    )


def test_session_routes_still_defines_compact():
    # The single live handler must remain.
    hits = _compact_route_decorators(ROUTES / "session_routes.py")
    names = [n for n, _ in hits]
    assert "compact_session" in names, (
        "session_routes.py must keep the live compact_session handler"
    )


def test_compact_route_defined_exactly_once_across_routers():
    total = 0
    for f in ("session_routes.py", "history_routes.py"):
        total += len(_compact_route_decorators(ROUTES / f))
    assert total == 1, (
        f"the compact route must be registered exactly once, found {total}"
    )
