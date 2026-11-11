import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _src(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _utility_kwargs(source: str, function_name: str) -> dict:
    tree = ast.parse(source)

    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    assert len(functions) == 1, function_name

    calls = [
        node for node in ast.walk(functions[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_endpoint"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "utility"
    ]
    assert len(calls) == 1, function_name

    return {
        kw.arg: ast.unparse(kw.value)
        for kw in calls[0].keywords
    }


def test_registered_manual_compaction_uses_session_owner_for_utility_endpoint():
    session_src = _src("routes/session_routes.py")

    assert 'owner = getattr(session, "owner", None) or effective_user(request)' in session_src
    kwargs = _utility_kwargs(session_src, "compact_session")
    assert kwargs["owner"] == "owner"
    assert kwargs["fallback_url"] == "getattr(session, 'endpoint_url', None)"
    assert kwargs["fallback_model"] == "getattr(session, 'model', None)"
    assert kwargs["fallback_headers"] == "getattr(session, 'headers', None)"


def test_task_name_generation_uses_owner_scoped_session_endpoint():
    src = _src("routes/task/task_routes.py")

    assert "async def _generate_task_name(prompt: str, owner: Optional[str] = None)" in src
    assert "q = q.filter(DbSession.owner == owner)" in src
    assert "headers = recent.headers or {}" in src
    assert "headers=headers" in src
    assert "await _generate_task_name(req.prompt, owner=user)" in src


def test_auto_compaction_utility_endpoint_keeps_chat_owner():
    helper_src = _src("routes/chat_helpers.py")
    compact_src = _src("src/context_compactor.py")

    assert "owner=user" in helper_src
    assert "owner: Optional[str] = None" in compact_src
    kwargs = _utility_kwargs(compact_src, "maybe_compact")
    assert kwargs["owner"] == "owner"
    assert kwargs["fallback_url"] == "endpoint_url"
    assert kwargs["fallback_model"] == "model"
    assert kwargs["fallback_headers"] == "headers"


def test_background_session_sort_uses_owner_task_endpoint():
    src = _src("src/session_actions.py")

    assert "resolve_task_endpoint(owner=owner or None)" in src


def test_scheduler_fallbacks_and_research_headers_are_owner_scoped():
    src = _src("src/task_scheduler.py")

    assert "resolve_task_candidates(" in src
    assert "owner=task.owner or None" in src
    assert 'resolve_endpoint(\n                    "research",' in src
    assert "owner=task.owner or None" in src
    assert "headers_from_resolver = False" in src
    assert "headers_from_resolver = True" in src
    assert "resolve_owner_registered_endpoint(db2, endpoint_url, task.owner or None)" in src
    assert "resolve_owner_registered_endpoint(db, endpoint_url, task.owner or None)" in src
    assert src.count("resolve_endpoint_runtime(ep, owner=task.owner or None)") == 2


def test_research_routes_fallbacks_are_owner_scoped():
    src = _src("routes/research/research_routes.py")

    assert 'resolve_endpoint("research", owner=user)' in src
    assert 'resolve_endpoint("utility", owner=user)' in src
    assert 'resolve_endpoint("default", owner=user)' in src
    assert 'resolve_endpoint("chat", owner=user)' in src
    assert '_merge(*resolve_endpoint("chat", owner=user))' in src
    assert '_merge(*resolve_endpoint("research", owner=user))' in src
    assert '_merge(*resolve_endpoint("utility", owner=user))' in src
    assert "ep = _owned_enabled_endpoint(db, user)" in src
    assert "db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).first()" not in src
    # _resolve_research_endpoint derives the scope from the session owner. The
    # rebased code generalized this to honor an explicit `owner` argument first
    # (``owner = owner or getattr(sess, "owner", None) or None``), so assert on
    # the stable session-derivation substring rather than the exact line.
    assert 'getattr(sess, "owner", None) or None' in src
