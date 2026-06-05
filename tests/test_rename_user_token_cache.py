"""Renaming a user must invalidate the bearer-token cache.

rename_user updates ApiToken.owner (and every other owner-scoped row) in the
DB, but the bearer-token cache in app.py still maps each token to the OLD
owner. Without invalidating it, the renamed user's API tokens keep resolving
to the old (now non-existent) owner and can no longer reach their data until
the cache happens to refresh. The route must invalidate the cache, like the
token CRUD routes do.
"""
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _route(router, name):
    for r in router.routes:
        if getattr(getattr(r, "endpoint", None), "__name__", "") == name:
            return r.endpoint
    raise AssertionError(name)


@pytest.fixture
def rename_endpoint(monkeypatch):
    import routes.auth_routes as ar
    import core.database as cdb

    # Neutralize the DB owner-rename loop (no real DB needed for this test).
    monkeypatch.setattr(cdb, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(cdb, "Base", SimpleNamespace(registry=SimpleNamespace(mappers=[])), raising=False)
    # Neutralize the JSON-prefs rename.
    pr = types.ModuleType("routes.prefs_routes")
    pr._load = lambda: {}
    pr._save = lambda d: None
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", pr)

    am = MagicMock()
    am.is_admin.return_value = True
    # The real _get_current_user closure resolves the admin via the auth
    # manager (a module-level monkeypatch can't intercept a closure), so drive
    # it through the manager instead.
    am.get_username_for_token.return_value = "admin"
    am.users = {"alice": {}}
    am.rename_user.return_value = True
    return _route(ar.setup_auth_routes(am), "rename_user"), am


def _request(invalidator):
    return SimpleNamespace(
        cookies={"odysseus_session": "t"},
        app=SimpleNamespace(state=SimpleNamespace(invalidate_token_cache=invalidator)),
        state=SimpleNamespace(current_user="admin"),
    )


def test_rename_invalidates_token_cache(rename_endpoint):
    import asyncio
    endpoint, _am = rename_endpoint
    called = {"n": 0}
    req = _request(lambda: called.__setitem__("n", called["n"] + 1))
    res = asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), req))
    assert res["ok"] is True and res["username"] == "alice2"
    assert called["n"] == 1, "bearer-token cache was not invalidated on rename"


def test_no_invalidator_does_not_crash(rename_endpoint):
    import asyncio
    endpoint, _am = rename_endpoint
    # app.state without the hook (older wiring) must not break rename.
    req = SimpleNamespace(cookies={"odysseus_session": "t"},
                          app=SimpleNamespace(state=SimpleNamespace()),
                          state=SimpleNamespace(current_user="admin"))
    res = asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), req))
    assert res["ok"] is True
