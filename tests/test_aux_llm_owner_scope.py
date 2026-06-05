from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _src(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_ai_tidy_resolves_endpoint_for_current_owner():
    src = _src("routes/document_routes.py")

    assert "user = get_current_user(request)" in src
    assert "resolve_task_endpoint(owner=user)" in src
    assert 'resolve_endpoint("default", owner=user)' in src


def test_calendar_quick_parse_resolves_endpoint_for_required_owner():
    src = _src("routes/calendar_routes.py")

    assert "owner = _require_user(request)" in src
    assert 'resolve_endpoint("utility", owner=owner)' in src
    assert 'resolve_endpoint("default", owner=owner)' in src


def test_manual_compaction_paths_use_request_owner_for_utility_endpoint():
    history_src = _src("routes/history_routes.py")
    session_src = _src("routes/session_routes.py")

    assert "owner = get_current_user(request)" in history_src
    assert 'resolve_endpoint("utility", owner=owner)' in history_src
    assert 'resolve_endpoint("utility", owner=get_current_user(request))' in session_src


def test_note_reminder_synthesis_uses_note_owner_endpoint():
    src = _src("routes/note_routes.py")

    assert "scoped_owner = owner or None" in src
    assert 'resolve_endpoint("utility", owner=scoped_owner)' in src
    assert 'resolve_endpoint("default", owner=scoped_owner)' in src


def test_task_route_llm_helpers_are_owner_scoped():
    src = _src("routes/task_routes.py")

    assert "async def _generate_task_name(prompt: str, owner: Optional[str] = None)" in src
    assert "q = q.filter(DbSession.owner == owner)" in src
    assert "headers = recent.headers or {}" in src
    assert "headers=headers" in src
    assert "await _generate_task_name(req.prompt, owner=user)" in src
    assert 'resolve_endpoint("utility", owner=user)' in src
    assert 'resolve_endpoint("default", owner=user)' in src


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

    assert "resolve_utility_fallback_candidates(owner=task.owner or None)" in src
    assert 'resolve_endpoint(\n                    "research",' in src
    assert "owner=task.owner or None" in src
    assert "headers_from_resolver = False" in src
    assert "headers_from_resolver = True" in src
    assert "from src.auth_helpers import owner_filter" in src
    assert "owner_filter(ep_q, ModelEndpoint, task.owner or None)" in src
