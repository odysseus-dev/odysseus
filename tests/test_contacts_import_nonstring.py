"""POST /api/contacts/import must not 500 on a non-string vcf/text/csv value.

`text = data.get("vcf") or ... or ""` left a non-string value (e.g. a number)
in place, so the next `text.strip()` raised AttributeError -> HTTP 500. The
handler now coerces with str() and degrades to a structured "no data" response.
"""
import asyncio

from routes.contacts_routes import setup_contacts_routes


def _import_handler():
    router = setup_contacts_routes()
    for route in router.routes:
        if getattr(route, "path", "").endswith("/import") and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("import route not found")


def _call(data):
    handler = _import_handler()
    return asyncio.run(handler(data=data, _admin="admin"))


def test_non_string_vcf_degrades_cleanly():
    resp = _call({"vcf": 123})
    assert resp["success"] is False
    assert "error" in resp


def test_non_string_csv_degrades_cleanly():
    resp = _call({"csv": ["a", "b"]})
    assert resp["success"] is False


def test_empty_body_reports_no_data():
    resp = _call({})
    assert resp["success"] is False
    assert resp["error"] == "No contact data found"
