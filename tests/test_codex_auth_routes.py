import asyncio
import os
import sys
import types
from types import SimpleNamespace

if "core" not in sys.modules:
    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")]
    sys.modules["core"] = core_pkg

from routes.codex_auth_routes import setup_codex_auth_routes
from src.codex_auth import set_codex_auth_service


class _AuthManager:
    is_configured = True

    def is_admin(self, user):
        return user == "admin"


def _request(user="admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=_AuthManager())),
    )


class _FakeService:
    async def status(self):
        return {"status": "not_authenticated"}

    async def start(self):
        return {"status": "pending", "verification_url": "https://auth.openai.com/codex/device", "user_code": "CODE-12345"}

    async def cancel(self):
        return {"status": "canceled"}

    async def logout(self):
        return {"status": "logged_out"}

    async def test(self):
        return {"ok": False, "status": "not_authenticated"}


def _endpoint(router, path, method):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_codex_auth_routes_use_service():
    set_codex_auth_service(_FakeService())
    try:
        router = setup_codex_auth_routes()
        start = _endpoint(router, "/api/codex-auth/start", "POST")
        out = asyncio.run(start(_request()))
        assert out["status"] == "pending"
        assert out["user_code"] == "CODE-12345"
    finally:
        set_codex_auth_service(None)


def test_codex_auth_routes_admin_gated():
    set_codex_auth_service(_FakeService())
    try:
        router = setup_codex_auth_routes()
        status = _endpoint(router, "/api/codex-auth/status", "GET")
        try:
            asyncio.run(status(_request(user="bob")))
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403
        else:
            raise AssertionError("non-admin request should fail")
    finally:
        set_codex_auth_service(None)
