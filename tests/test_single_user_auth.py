"""Pin the single-user / AUTH_ENABLED=false fix so it doesn't regress.

With auth disabled (the DEFAULT single-user deployment) the auth middleware
never sets `request.state.current_user`, so `get_current_user()` returns None.
Several list/save/research/gallery endpoints used to hard-raise 401/403 in that
case ("Authentication required" / "Not authenticated"), breaking the app for
its primary, intended use. The tolerant `require_user()` helper returns "" for
a loopback caller when auth is unconfigured and only raises when auth IS
configured — these endpoints must use it and must NOT 401/403 in single-user
mode.

Companion to test_auth_regressions.py (which pins the *configured* case still
rejecting anonymous callers). Together they guard both directions: auth-off must
work, auth-on must still gate.
"""

import os
import sys
import types
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

# Stub `core.database` / `core.auth` / endpoint_resolver before the route
# modules import them. Same trick as test_auth_regressions.py — the real
# modules instantiate SQLAlchemy declarative classes at import-time which blow
# up under the conftest's `sqlalchemy.*` MagicMock stubs.
for _stub, _attrs in [
    ("core.database", dict(
        SessionLocal=MagicMock(), ScheduledTask=MagicMock(), TaskRun=MagicMock(),
        ModelEndpoint=MagicMock(), Session=MagicMock(), ChatMessage=MagicMock(),
        CalendarCal=MagicMock(), CalendarEvent=MagicMock(),
        Document=MagicMock(), DocumentVersion=MagicMock(),
        GalleryImage=MagicMock(), GalleryAlbum=MagicMock(), Note=MagicMock(),
        McpServer=MagicMock(),
    )),
    ("core.auth", dict(AuthManager=MagicMock())),
    ("src.endpoint_resolver", dict(
        resolve_endpoint=MagicMock(return_value=("", "", {})),
        normalize_base=MagicMock(), build_chat_url=MagicMock(),
        build_headers=MagicMock(),
    )),
]:
    if _stub not in sys.modules:
        m = types.ModuleType(_stub)
        # Point a real parent path so unstubbed submodules still resolve.
        if "." in _stub:
            parent_name = _stub.rpartition(".")[0]
            if parent_name not in sys.modules:
                parent = types.ModuleType(parent_name)
                real_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    *parent_name.split("."),
                )
                parent.__path__ = [real_path] if os.path.isdir(real_path) else []
                sys.modules[parent_name] = parent
        for k, v in _attrs.items():
            setattr(m, k, v)
        sys.modules[_stub] = m

from fastapi import HTTPException


def _single_user_request():
    """Stand-in fastapi.Request for the DEFAULT single-user deployment:

    - middleware never set current_user (auth disabled)  -> None
    - no auth_manager configured on app.state            -> require_user falls
                                                            through to loopback
    - loopback client                                    -> require_user -> ""
    """
    req = SimpleNamespace()
    req.state = SimpleNamespace(current_user=None)
    req.app = SimpleNamespace(state=SimpleNamespace())  # no auth_manager attr
    req.client = SimpleNamespace(host="127.0.0.1")
    return req


def _maybe_call(fn, **kwargs):
    """Call a (possibly async) route fn, returning either its result or the
    HTTPException it raised. Other exceptions (DB MagicMock noise) propagate as
    None — we only care that auth itself didn't reject the caller."""
    try:
        out = fn(**kwargs)
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        return out, None
    except HTTPException as exc:
        return None, exc
    except Exception:
        # Non-auth failure (DB stub blew up downstream). The auth gate ran
        # first and let us through, which is all this test asserts.
        return None, None


def _assert_not_auth_rejected(exc):
    if exc is not None:
        assert exc.status_code not in (401, 403), (
            f"single-user mode was auth-rejected with {exc.status_code}: {exc.detail}"
        )


# ---------------------------------------------------------------------------
# require_user itself — the contract these routes rely on
# ---------------------------------------------------------------------------

def test_require_user_returns_empty_string_in_single_user_mode():
    from src.auth_helpers import require_user
    assert require_user(_single_user_request()) == ""


def test_require_user_rejects_non_loopback_when_unconfigured():
    from src.auth_helpers import require_user
    req = _single_user_request()
    req.client = SimpleNamespace(host="10.0.0.5")  # off-box, unconfigured auth
    with pytest.raises(HTTPException) as exc:
        require_user(req)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# research — _require_user (was a hard 401)
# ---------------------------------------------------------------------------

def test_research_active_not_rejected_single_user():
    from routes.research_routes import setup_research_routes
    rh = MagicMock()
    rh._active_tasks = {}
    router = setup_research_routes(rh)
    target = next(r.endpoint for r in router.routes
                  if getattr(r, "path", "") == "/api/research/active")
    out, exc = _maybe_call(target, request=_single_user_request())
    _assert_not_auth_rejected(exc)
    # With no active tasks it returns an empty list, not a 401.
    assert out == {"active": []}


# ---------------------------------------------------------------------------
# documents — list_documents (was a hard 403 "Authentication required")
# ---------------------------------------------------------------------------

def test_list_documents_not_rejected_single_user():
    from routes.document_routes import setup_document_routes
    router = setup_document_routes(session_manager=MagicMock())
    target = next(r.endpoint for r in router.routes
                  if getattr(r, "path", "") == "/api/documents/{session_id}")
    _, exc = _maybe_call(target, request=_single_user_request(), session_id="s1")
    _assert_not_auth_rejected(exc)


# ---------------------------------------------------------------------------
# gallery — gallery_download_zip (was a hard 401 "Not authenticated")
# ---------------------------------------------------------------------------

def test_gallery_download_zip_not_rejected_single_user():
    from routes.gallery_routes import setup_gallery_routes
    router = setup_gallery_routes()
    target = next(r.endpoint for r in router.routes
                  if getattr(r, "path", "") == "/api/gallery/download-zip")
    # Empty body -> 400 "No images specified" is fine; what must NOT happen is
    # a 401 before we even look at the body.
    _, exc = _maybe_call(target, request=_single_user_request())
    _assert_not_auth_rejected(exc)


# ---------------------------------------------------------------------------
# sessions — list_archived_sessions (was 403) and sessions_save_now (was 401)
# ---------------------------------------------------------------------------

def _session_router():
    from routes.session_routes import setup_session_routes
    return setup_session_routes(session_manager=MagicMock(),
                                config={"SESSIONS_FILE": "sessions.json"})


def test_list_archived_sessions_not_rejected_single_user():
    router = _session_router()
    target = next(r.endpoint for r in router.routes
                  if getattr(r, "path", "") == "/api/sessions/archived")
    _, exc = _maybe_call(target, request=_single_user_request())
    _assert_not_auth_rejected(exc)


def test_sessions_save_now_not_rejected_single_user():
    router = _session_router()
    target = next(r.endpoint for r in router.routes
                  if getattr(r, "path", "") == "/api/sessions/save")
    out, exc = _maybe_call(target, request=_single_user_request())
    _assert_not_auth_rejected(exc)
    # Save succeeds in single-user mode rather than 401ing.
    assert out == {"ok": True, "path": "sessions.json"}
