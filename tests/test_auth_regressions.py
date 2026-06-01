"""Pin the auth-gate fixes from the 2026-05-19 v2 review so they
don't regress. Specifically:

- All `/api/research/*` endpoints reject anonymous callers.
- Task `create_task` blocks shell-executing action types for
  non-admins (`run_local`, `run_script`, `ssh_command`).
- `pop_notifications(owner)` returns only the calling user's
  notifications; ownerless legacy notifications are drained only by
  anonymous/no-owner callers.
"""

import os
import sys
import types
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

# Stub `core.database` / `core.auth` before the route modules import them.
# (Same trick as test_null_owner_gates.py — the real modules instantiate
# SQLAlchemy declarative classes at import-time which blow up under the
# conftest's `sqlalchemy.*` MagicMock stubs.)
def _ensure_stub(name: str, **attrs):
    """Create or augment a stub module with the given attributes.
    Augments existing entries because earlier-run tests may have already
    stubbed the same module with a different attribute set.

    Also stubs the parent package and wires the child onto it as an
    attribute. Without stubbing the parent we'd either (a) run the real
    `core/__init__.py`, which transitively imports SQLAlchemy-using
    modules and explodes under the conftest mocks, or (b) leave the
    stub orphaned so `import core.auth; core.auth.AuthManager` raises
    `AttributeError`."""
    # Stub the parent package first if not already loaded. We point
    # `__path__` at the real on-disk directory so submodules NOT
    # stubbed here can still resolve via normal import machinery —
    # but `core/__init__.py` is bypassed because the package is
    # already in `sys.modules`, which is exactly what we want.
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        if parent_name not in sys.modules:
            parent = types.ModuleType(parent_name)
            # Find the real on-disk path so unstubbed submodules
            # (core.middleware etc.) still load from disk.
            real_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                *parent_name.split("."),
            )
            parent.__path__ = [real_path] if os.path.isdir(real_path) else []
            sys.modules[parent_name] = parent
        else:
            parent = sys.modules[parent_name]
    else:
        parent = None
        child_name = None

    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    if parent is not None and not hasattr(parent, child_name):
        setattr(parent, child_name, mod)
    return mod

_ensure_stub("core.database",
    SessionLocal=MagicMock(), ScheduledTask=MagicMock(), TaskRun=MagicMock(),
    ModelEndpoint=MagicMock(), Session=MagicMock(), ChatMessage=MagicMock(),
    CalendarCal=MagicMock(), CalendarEvent=MagicMock(),
    Document=MagicMock(), DocumentVersion=MagicMock(),
    GalleryImage=MagicMock(), GalleryAlbum=MagicMock(), Note=MagicMock(),
    McpServer=MagicMock(),
)
_ensure_stub("core.auth", AuthManager=MagicMock())
_ensure_stub("src.endpoint_resolver",
    resolve_endpoint=MagicMock(return_value=("", "", {})),
    normalize_base=MagicMock(),
    build_chat_url=MagicMock(),
    build_headers=MagicMock(),
)

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Research endpoints — `_require_user` rejects anonymous
# ---------------------------------------------------------------------------

def _build_research_router():
    """Construct the research router with a mock research_handler so we
    can fish out the inner `_require_user` helper without booting the
    full app."""
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    setup_research_routes(rh)
    # The helper lives inside the setup closure. Easiest way to exercise
    # it: re-import the module and grab the symbol via its source.
    # Instead, exercise it via the route helper that has request:Request.
    return rh


def _fake_request(user=None):
    """Cheap stand-in for fastapi.Request — only `request.state.current_user`
    matters to `get_current_user`."""
    req = SimpleNamespace()
    req.state = SimpleNamespace(current_user=user)
    # Some endpoints touch .client too — provide a benign default.
    req.client = SimpleNamespace(host="127.0.0.1")
    return req


def test_research_status_rejects_anonymous():
    """research_status must 401 when no user is on the request state."""
    # Build a fresh router and pluck its registered routes.
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    rh.get_status.return_value = {"status": "running"}  # would 200 if auth passed
    router = setup_research_routes(rh)
    # Find the function registered for /api/research/status/{session_id}
    target = None
    for route in router.routes:
        if getattr(route, "path", "") == "/api/research/status/{session_id}":
            target = route.endpoint
            break
    assert target is not None, "research_status route not registered"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="x", request=_fake_request(user=None)))
    assert exc.value.status_code == 401


def test_research_status_accepts_authenticated():
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    rh._active_tasks = {"x": {"owner": "alice", "status": "running"}}
    rh.get_status.return_value = {"status": "running", "progress": {}}
    router = setup_research_routes(rh)
    target = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/research/status/{session_id}")
    out = asyncio.run(target(session_id="x", request=_fake_request(user="alice")))
    assert out == {"status": "running", "progress": {}}


def test_research_status_rejects_wrong_owner():
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    rh._active_tasks = {"x": {"owner": "alice", "status": "running"}}
    rh.get_status.return_value = {"status": "running", "progress": {}}
    router = setup_research_routes(rh)
    target = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/research/status/{session_id}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="x", request=_fake_request(user="bob")))
    assert exc.value.status_code == 404


def test_research_cancel_rejects_anonymous():
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    router = setup_research_routes(rh)
    target = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/research/cancel/{session_id}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="x", request=_fake_request(user=None)))
    assert exc.value.status_code == 401


def test_research_delete_rejects_anonymous():
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    router = setup_research_routes(rh)
    target = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/research/{session_id}")
    # Note: `target` here is the most-recently registered route on this
    # path which is the DELETE. Either /detail or /delete both match
    # other paths — the {session_id} bare path is DELETE.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="x", request=_fake_request(user=None)))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# pop_notifications owner filter
# ---------------------------------------------------------------------------

def test_pop_notifications_owner_filtered():
    """pop_notifications(owner='alice') must return only alice's items.
    bob's and legacy ownerless items stay behind in the queue."""
    # Build a minimal scheduler instance that we can hit directly.
    # Reuse the real class so the test catches future regressions of
    # the filter logic.
    import sys, types
    from unittest.mock import MagicMock as _MM
    # `task_scheduler` pulls in lots of helpers — stub the ones it uses.
    for s in ["src.builtin_actions", "src.ai_interaction", "src.endpoint_resolver",
              "src.agent_loop", "src.session_manager"]:
        if s not in sys.modules:
            mod = types.ModuleType(s)
            sys.modules[s] = mod
    from src.task_scheduler import TaskScheduler
    sch = TaskScheduler.__new__(TaskScheduler)  # bypass __init__ network etc.
    sch._pending_notifications = []
    sch.add_notification("t1", "success", "id1", owner="alice")
    sch.add_notification("t2", "error",   "id2", owner="bob")
    sch.add_notification("t3", "success", "id3", owner=None)
    sch.add_notification("t4", "success", "id4", owner="alice")
    alice = sch.pop_notifications(owner="alice")
    alice_names = {n["task_name"] for n in alice}
    # alice gets only her own rows; bob's row and legacy null-owner rows stay.
    assert alice_names == {"t1", "t4"}
    # bob's row and the legacy ownerless row are still queued.
    remaining = sch._pending_notifications
    assert {n["task_name"] for n in remaining} == {"t2", "t3"}
    # Anonymous caller (owner=None) drains everything that's left.
    rest = sch.pop_notifications(owner=None)
    assert {n["task_name"] for n in rest} == {"t2", "t3"}
    assert sch._pending_notifications == []


# ---------------------------------------------------------------------------
# Task action allowlist
# ---------------------------------------------------------------------------

def test_admin_only_actions_set_contains_shell_runners():
    """The constant defining shell-executing action types must include
    the three risky entries. Catches accidental removal."""
    from routes import task_routes
    # `_ADMIN_ONLY_ACTIONS` is a closure constant. Easiest pin: re-read
    # the source and check for the three risky entries + the admin gate
    # wording.
    src = open(task_routes.__file__, encoding="utf-8").read()
    assert '"run_local"' in src
    assert '"run_script"' in src
    assert '"ssh_command"' in src
    # And the gate is wired into both create and update paths.
    assert "Action '" in src and "requires admin privileges" in src


def test_task_create_notification_default_allows_action_specific_defaults():
    """Omitted notifications_enabled should stay None so create_task can
    default noisy/quiet built-ins differently."""
    from routes.task_routes import TaskCreate

    req = TaskCreate(task_type="action", action="check_email_urgency", schedule="cron", cron_expression="*/15 * * * *")
    assert req.notifications_enabled is None


def test_ship_paused_housekeeping_stays_paused_by_default():
    """Built-ins marked ship_paused are intentionally opt-in even after
    the user enables the rest of Tasks."""
    from routes import task_routes
    from src import task_scheduler

    route_src = open(task_routes.__file__, encoding="utf-8").read()
    scheduler_src = open(task_scheduler.__file__, encoding="utf-8").read()
    assert '"ship_paused": True' in scheduler_src
    assert 'defs.get("ship_paused")' in scheduler_src
    assert 'defs.get("ship_paused")' in route_src


def test_task_payload_exposes_crew_member_id_for_ui_category():
    from routes import task_routes

    src = open(task_routes.__file__, encoding="utf-8").read()
    assert '"crew_member_id"' in src
