"""Regression: POST /api/contacts/add must not crash when name/email is JSON null.

The handler did `data.get("name", "").strip()`. dict.get returns the default
only when the key is ABSENT; a body like {"name": null, "email": "x@y.com"}
gives name=None, so None.strip() raised AttributeError -> 500. Now guarded with
`(data.get("name") or "")`.
"""
import asyncio

import pytest

import routes.contacts_routes as cr


def _add_handler():
    router = cr.setup_contacts_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/add") and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("add_contact route not found")


@pytest.fixture
def _stub_store(monkeypatch):
    created = []
    monkeypatch.setattr(cr, "_fetch_contacts", lambda *a, **k: [])
    monkeypatch.setattr(
        cr,
        "_create_contact",
        lambda name, email="", address="", phones=None: created.append((name, email, address, phones or [])) or True,
    )
    return created


def test_null_name_does_not_crash(_stub_store):
    handler = _add_handler()
    result = asyncio.run(handler({"name": None, "email": "x@y.com"}, _admin="admin"))
    assert result["success"] is True
    # name fell back to the email local-part instead of crashing.
    assert _stub_store == [("x", "x@y.com", "", [])]


def test_null_email_does_not_crash(_stub_store):
    handler = _add_handler()
    result = asyncio.run(handler({"name": "Bob", "email": None}, _admin="admin"))
    assert result["success"] is True
    assert _stub_store == [("Bob", "", "", [])]


def test_phone_only_contact_is_allowed(_stub_store):
    handler = _add_handler()
    result = asyncio.run(handler({"name": "Bob", "email": None, "phone": "0805412 7841"}, _admin="admin"))
    assert result["success"] is True
    assert _stub_store == [("Bob", "", "", ["0805412 7841"])]
