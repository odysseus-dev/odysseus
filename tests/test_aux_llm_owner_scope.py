from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _src(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_registered_manual_compaction_uses_session_owner_for_utility_endpoint():
    session_src = _src("routes/session_routes.py")

    assert 'owner = getattr(session, "owner", None) or effective_user(request)' in session_src
    assert 'resolve_endpoint("utility", owner=owner)' in session_src


def test_task_name_generation_uses_owner_scoped_session_endpoint():
    src = _src("routes/task_routes.py")

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
    assert 'resolve_endpoint("utility", owner=owner)' in compact_src


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
    assert "from src.auth_helpers import owner_filter" in src
    assert "owner_filter(ep_q, ModelEndpoint, task.owner or None)" in src


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
