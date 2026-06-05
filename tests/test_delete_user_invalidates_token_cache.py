"""Deleting a user must invalidate the bearer-token cache.

delete_user removes the user's ApiToken rows from the DB, but the bearer-auth
middleware in app.py serves from an in-memory prefix->token cache that only
rebuilds when flagged dirty (app.state.invalidate_token_cache). If the admin
delete route does not flag it, a deleted user's already-cached token keeps
authenticating until some unrelated token op or a process restart clears the
cache. The DELETE /api/auth/users handler now calls the invalidator on a
successful delete (and only then), so the next bearer request rebuilds the
cache from the DB, where the rows are already gone, and the token is rejected.
"""
import asyncio
import types

from routes.auth_routes import setup_auth_routes, DeleteUserRequest


def _handler(router):
    for route in router.routes:
        if getattr(route, "path", "") == "/api/auth/users" and "DELETE" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("DELETE /api/auth/users handler not found")


def _fake_request(invalidations):
    state = types.SimpleNamespace(invalidate_token_cache=lambda: invalidations.append(True))
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(cookies={"_dummy": "x"}, app=app)


def _auth_manager(delete_result):
    return types.SimpleNamespace(
        get_username_for_token=lambda token: "admin",
        is_admin=lambda user: True,
        delete_user=lambda username, requesting_user: delete_result,
    )


def test_successful_delete_invalidates_cache():
    invalidations = []
    router = setup_auth_routes(_auth_manager(delete_result=True))
    handler = _handler(router)
    result = asyncio.run(handler(DeleteUserRequest(username="bob"), _fake_request(invalidations)))
    assert result == {"ok": True}
    assert invalidations == [True], "successful delete must flag the token cache stale"


def test_refused_delete_does_not_invalidate_cache():
    invalidations = []
    router = setup_auth_routes(_auth_manager(delete_result=False))
    handler = _handler(router)
    try:
        asyncio.run(handler(DeleteUserRequest(username="admin"), _fake_request(invalidations)))
        raised = False
    except Exception:
        raised = True
    assert raised, "a refused delete should raise (HTTP 400)"
    assert invalidations == [], "a refused delete must not touch the token cache"
