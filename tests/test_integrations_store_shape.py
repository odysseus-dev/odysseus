import json
import asyncio
from types import SimpleNamespace

import pytest

from src import integrations


def test_load_integrations_skips_non_object_rows(tmp_path, monkeypatch):
    data_file = tmp_path / "integrations.json"
    data_file.write_text(json.dumps([{"id": "good", "name": "Good"}, "bad", None]))
    monkeypatch.setattr(integrations, "DATA_FILE", str(data_file))

    assert integrations.load_integrations() == [{"id": "good", "name": "Good"}]


@pytest.fixture
def integrations_routes(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from routes import auth_routes

    monkeypatch.setattr(integrations, "DATA_FILE", str(tmp_path / "integrations.json"))
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)

    class _AuthManager:
        def get_username_for_token(self, token):
            return "admin" if token == "session-token" else None

        def is_admin(self, user):
            return user == "admin"

    router = auth_routes.setup_auth_routes(_AuthManager())

    def endpoint(path, method):
        for route in router.routes:
            if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
                return route.endpoint
        raise AssertionError(f"{method} {path} route not registered")

    return endpoint, auth_routes.SESSION_COOKIE, fastapi.HTTPException


class _JsonRequest(SimpleNamespace):
    def __init__(self, body, session_cookie):
        super().__init__(
            cookies={session_cookie: "session-token"},
            client=SimpleNamespace(host="127.0.0.1"),
            _body=body,
        )

    async def json(self):
        return self._body


@pytest.mark.parametrize("blank_name", ["", "   "])
def test_create_integration_rejects_blank_name_without_persisting(integrations_routes, blank_name):
    endpoint, session_cookie, http_exception = integrations_routes
    create_integration = endpoint("/api/auth/integrations", "POST")

    with pytest.raises(http_exception) as exc:
        asyncio.run(create_integration(
            _JsonRequest({"name": blank_name, "base_url": "https://example.test"}, session_cookie)
        ))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Integration name is required"
    assert integrations.load_integrations() == []


@pytest.mark.parametrize("blank_base_url", ["", "   "])
def test_create_integration_rejects_blank_base_url_without_persisting(integrations_routes, blank_base_url):
    endpoint, session_cookie, http_exception = integrations_routes
    create_integration = endpoint("/api/auth/integrations", "POST")

    with pytest.raises(http_exception) as exc:
        asyncio.run(create_integration(
            _JsonRequest({"name": "Example", "base_url": blank_base_url}, session_cookie)
        ))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Integration base URL is required"
    assert integrations.load_integrations() == []


@pytest.mark.parametrize(("base_url", "message"), [
    ("ftp://example.test", "Integration base URL must be an HTTP(S) URL"),
    ("https://example.test/api?token=abc", "Integration base URL must not include query or fragment"),
    ("https://example.test/api#fragment", "Integration base URL must not include query or fragment"),
])
def test_create_integration_rejects_invalid_base_url_without_persisting(
    integrations_routes, base_url, message
):
    endpoint, session_cookie, http_exception = integrations_routes
    create_integration = endpoint("/api/auth/integrations", "POST")

    with pytest.raises(http_exception) as exc:
        asyncio.run(create_integration(
            _JsonRequest({"name": "Example", "base_url": base_url}, session_cookie)
        ))

    assert exc.value.status_code == 400
    assert exc.value.detail == message
    assert integrations.load_integrations() == []


@pytest.mark.parametrize("blank_name", ["", "   "])
def test_update_integration_rejects_blank_name_without_changing_existing(integrations_routes, blank_name):
    endpoint, session_cookie, http_exception = integrations_routes
    update_integration = endpoint("/api/auth/integrations/{integration_id}", "PUT")
    integrations.save_integrations([
        {
            "id": "existing",
            "name": "Original",
            "base_url": "https://example.test",
        }
    ])

    with pytest.raises(http_exception) as exc:
        asyncio.run(update_integration(
            integration_id="existing",
            request=_JsonRequest({"name": blank_name}, session_cookie),
        ))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Integration name is required"
    assert integrations.load_integrations()[0]["name"] == "Original"


@pytest.mark.parametrize("blank_base_url", ["", "   "])
def test_update_integration_rejects_blank_base_url_without_changing_existing(integrations_routes, blank_base_url):
    endpoint, session_cookie, http_exception = integrations_routes
    update_integration = endpoint("/api/auth/integrations/{integration_id}", "PUT")
    integrations.save_integrations([
        {
            "id": "existing",
            "name": "Original",
            "base_url": "https://example.test",
        }
    ])

    with pytest.raises(http_exception) as exc:
        asyncio.run(update_integration(
            integration_id="existing",
            request=_JsonRequest({"base_url": blank_base_url}, session_cookie),
        ))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Integration base URL is required"
    assert integrations.load_integrations()[0]["base_url"] == "https://example.test"


@pytest.mark.parametrize(("base_url", "message"), [
    ("ftp://example.test", "Integration base URL must be an HTTP(S) URL"),
    ("https://example.test/api?token=abc", "Integration base URL must not include query or fragment"),
    ("https://example.test/api#fragment", "Integration base URL must not include query or fragment"),
])
def test_update_integration_rejects_invalid_base_url_without_changing_existing(
    integrations_routes, base_url, message
):
    endpoint, session_cookie, http_exception = integrations_routes
    update_integration = endpoint("/api/auth/integrations/{integration_id}", "PUT")
    integrations.save_integrations([
        {
            "id": "existing",
            "name": "Original",
            "base_url": "https://example.test",
        }
    ])

    with pytest.raises(http_exception) as exc:
        asyncio.run(update_integration(
            integration_id="existing",
            request=_JsonRequest({"base_url": base_url}, session_cookie),
        ))

    assert exc.value.status_code == 400
    assert exc.value.detail == message
    assert integrations.load_integrations()[0]["base_url"] == "https://example.test"
