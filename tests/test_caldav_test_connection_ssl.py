"""Regression: CalDAV test_connection must trust the operator's CA bundle.

The pre-flight used httpx with trust_env=False, which ignored
SSL_CERT_FILE/REQUESTS_CA_BUNDLE. Self-signed CalDAV servers that the
real sync accepts (via caldav lib -> requests -> honors bundle) were
rejected by the test with CERTIFICATE_VERIFY_FAILED.

These tests exercise the *route handler* directly (via ASGI TestClient)
and capture the verify= kwarg passed to httpx.AsyncClient, ensuring the
route code — not a test-side duplicate — builds the SSL context correctly.
"""
import os
import ssl
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

# No module-level sys.modules stubbing here: conftest pre-imports the real
# sqlalchemy/core.database, and stubbing extras (e.g. caldav) at collection
# time leaks MagicMocks into later tests in the same process — it made
# test_caldav_redirect_hardening's real DAVClient a mock that never sent
# the PROPFIND. The route's lazy imports are patched per-request instead.


def _fake_response(status_code=207, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.calendar_routes import setup_calendar_routes

    with patch("routes.calendar_routes._require_user", return_value="test-owner"):
        router = setup_calendar_routes()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_fake_async_client(captured):
    """Return a fake httpx.AsyncClient class that captures constructor kwargs."""
    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def request(self, *a, **kw):
            return _fake_response(207)

    return FakeAsyncClient


def _post_test(client, captured, env=None):
    """POST /api/calendar/test with credentials in body so no DB lookup needed.

    Patches httpx.AsyncClient at the real module level so the route's
    ``import httpx; httpx.AsyncClient(...)`` picks up the fake class.
    Also stubs validate_caldav_url (lazy-imported from src.caldav_sync).
    """
    fake_cls = _make_fake_async_client(captured)

    # Stub the caldav_sync module so the lazy `from src.caldav_sync import validate_caldav_url`
    # inside the route body resolves to a pass-through.
    caldav_sync_stub = MagicMock()
    caldav_sync_stub.validate_caldav_url = lambda u: u

    ctx_managers = [
        patch.object(httpx, "AsyncClient", fake_cls),
        patch.dict(sys.modules, {"src.caldav_sync": caldav_sync_stub}),
        patch("routes.calendar_routes._require_user", return_value="test-owner"),
    ]
    if env is not None:
        ctx_managers.append(patch.dict(os.environ, env))

    # Enter all context managers
    for cm in ctx_managers:
        cm.__enter__()
    try:
        return client.post(
            "/api/calendar/test",
            json={"url": "https://cal.example.com", "username": "u", "password": "p"},
        )
    finally:
        for cm in reversed(ctx_managers):
            cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Route-level tests
# ---------------------------------------------------------------------------

def test_route_passes_ssl_context_with_correct_flags(client):
    """The route must pass an ssl.SSLContext to httpx.AsyncClient(verify=...)
    with trust_env=False, follow_redirects=False, and VERIFY_X509_STRICT cleared."""
    captured = {}
    resp = _post_test(client, captured)

    assert resp.status_code == 200
    assert isinstance(captured.get("verify"), ssl.SSLContext), (
        f"verify= should be an ssl.SSLContext, got {type(captured.get('verify'))}"
    )
    assert captured.get("trust_env") is False
    assert captured.get("follow_redirects") is False
    ctx = captured["verify"]
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT), (
        "VERIFY_X509_STRICT must be cleared for self-signed CA compat"
    )


def test_route_ssl_cert_file_takes_precedence(client, tmp_path):
    """SSL_CERT_FILE is the exact bundle loaded when both variables are set."""
    bundle_a = tmp_path / "ssl-cert-file.pem"
    bundle_b = tmp_path / "requests-ca-bundle.pem"
    bundle_a.write_text("ssl-cert-file", encoding="utf-8")
    bundle_b.write_text("requests-ca-bundle", encoding="utf-8")

    loaded = []

    class FakeSSLContext:
        def __init__(self):
            self.verify_flags = ssl.VERIFY_X509_STRICT

        def load_verify_locations(self, cafile=None, capath=None, cadata=None):
            loaded.append(
                {
                    "cafile": cafile,
                    "capath": capath,
                    "cadata": cadata,
                }
            )

    ssl_context = FakeSSLContext()
    captured = {}
    env = {
        "SSL_CERT_FILE": str(bundle_a),
        "REQUESTS_CA_BUNDLE": str(bundle_b),
    }

    with patch.object(
        ssl,
        "create_default_context",
        return_value=ssl_context,
    ):
        resp = _post_test(client, captured, env=env)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert loaded == [
        {
            "cafile": str(bundle_a),
            "capath": None,
            "cadata": None,
        }
    ]
    assert captured.get("verify") is ssl_context
    assert captured.get("trust_env") is False
    assert captured.get("follow_redirects") is False
    assert not (
        ssl_context.verify_flags & ssl.VERIFY_X509_STRICT
    )


def test_route_missing_bundle_does_not_crash(client):
    """A nonexistent CA bundle path must not crash -- fall back to system CAs."""
    captured = {}
    resp = _post_test(client, captured, env={"SSL_CERT_FILE": "/nonexistent/ca-bundle.pem"})

    assert resp.status_code == 200
    ctx = captured["verify"]
    assert isinstance(ctx, ssl.SSLContext)
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)


def test_route_empty_env_vars_use_system_defaults(client):
    """Empty SSL_CERT_FILE and REQUESTS_CA_BUNDLE should not crash."""
    captured = {}
    resp = _post_test(client, captured, env={"SSL_CERT_FILE": "", "REQUESTS_CA_BUNDLE": ""})

    assert resp.status_code == 200
    ctx = captured["verify"]
    assert isinstance(ctx, ssl.SSLContext)
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)
