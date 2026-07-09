"""User-supplied IMAP/SMTP ports must not crash the email-account endpoints.

A non-numeric port (for example ``"imap"`` or ``"993x"``) previously reached an
unguarded ``int(...)`` in create / update / test-config and raised ``ValueError``,
which surfaces as an HTTP 500. The endpoints should reject it with their standard
``{"ok": False, "error": ...}`` response instead.
"""

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_coerce_port_accepts_int_and_numeric_string():
    import routes.email_routes as email_routes
    assert email_routes._coerce_port(2525, 993) == (2525, None)
    assert email_routes._coerce_port("465", 993) == (465, None)


def test_coerce_port_blank_uses_default():
    import routes.email_routes as email_routes
    assert email_routes._coerce_port(None, 993) == (993, None)
    assert email_routes._coerce_port("", 465) == (465, None)


def test_coerce_port_rejects_non_numeric():
    import routes.email_routes as email_routes
    port, err = email_routes._coerce_port("imap", 993)
    assert port is None
    assert err and "port" in err.lower()


@pytest.mark.asyncio
async def test_create_account_rejects_non_numeric_port():
    """A bad port is rejected before any DB work, with the endpoint's error shape."""
    import routes.email_routes as email_routes
    router = email_routes.setup_email_routes()
    create = _route_endpoint(router, "/api/email/accounts", "POST")
    result = await create(
        {
            "name": "Test",
            "imap_host": "mail.example.com",
            "imap_user": "u",
            "imap_password": "p",
            "imap_port": "not-a-number",
        },
        owner="alice",
    )
    assert result["ok"] is False
    assert "port" in result["error"].lower()
