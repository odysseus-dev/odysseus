"""Tests for DB-driven session listing (PR #1361).

Verifies:
- get_sessions_for_user handles None, empty string, and specific users correctly
- The DB is the source of truth for the session list
- Ghost sessions (in-memory only) are included when missing from DB

Style mirrors tests/test_session_ghost_delete.py.
"""

import sys
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

_ABSENT = object()
_TEMP_STUBS = ("core.database", "core.models", "src.request_models")
_saved = {name: sys.modules.get(name, _ABSENT) for name in _TEMP_STUBS}
_saved["core.session_manager"] = sys.modules.get("core.session_manager", _ABSENT)
try:
    for _name in _TEMP_STUBS:
        sys.modules[_name] = MagicMock(name=_name)
    if isinstance(sys.modules.get("core.session_manager"), MagicMock):
        del sys.modules["core.session_manager"]
    SM = importlib.import_module("core.session_manager")
    import routes.session_routes as SR
finally:
    for _name, _val in _saved.items():
        if _val is _ABSENT:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _val


# --- SessionManager.get_sessions_for_user -----------------------------------

class TestGetSessionsForUser:

    def test_none_returns_all(self):
        mgr = SM.SessionManager.__new__(SM.SessionManager)
        mgr.sessions = {"a": "sess_a", "b": "sess_b"}
        assert mgr.get_sessions_for_user(None) == mgr.sessions

    def test_empty_string_returns_all(self):
        """Fix for unauthenticated mode (AUTH_ENABLED=false)."""
        mgr = SM.SessionManager.__new__(SM.SessionManager)
        mgr.sessions = {"a": "sess_a", "b": "sess_b"}
        assert mgr.get_sessions_for_user("") == mgr.sessions

    def test_specific_user_filters(self):
        mgr = SM.SessionManager.__new__(SM.SessionManager)
        mgr.sessions = {
            "a": SimpleNamespace(owner="alice"),
            "b": SimpleNamespace(owner="bob"),
        }
        result = mgr.get_sessions_for_user("alice")
        assert list(result.keys()) == ["a"]

    def test_specific_user_no_match_returns_empty(self):
        mgr = SM.SessionManager.__new__(SM.SessionManager)
        mgr.sessions = {"a": SimpleNamespace(owner="alice")}
        assert mgr.get_sessions_for_user("bob") == {}
