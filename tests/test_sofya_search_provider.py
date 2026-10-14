"""Sofya search provider: request shape, result mapping, error handling, settings."""

from types import SimpleNamespace

import httpx
import pytest

import routes.auth_routes as auth_routes
import src.settings as settings_mod
from services.search import core, providers


class _Response:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://sofya.co/v1/search")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )

    def json(self):
        return self._body


@pytest.fixture
def calls(monkeypatch):
    settings = {}
    monkeypatch.setattr(providers, "_get_search_settings", lambda: settings)
    monkeypatch.delenv("SOFYA_API_KEY", raising=False)
    seen = SimpleNamespace(posts=[], response=_Response(), settings=settings)

    def _post(url, **kwargs):
        seen.posts.append((url, kwargs))
        return seen.response

    monkeypatch.setattr(providers.httpx, "post", _post)
    return seen


def test_sofya_maps_results_and_sends_request(calls):
    calls.settings["sofya_api_key"] = "ay_live_test"
    calls.response = _Response(body={"results": [
        {"title": "A", "url": "https://a.example", "content": "a text",
         "description": "a snippet", "published_date": "2026-10-01"},
        {"title": "No URL", "url": "", "description": "dropped"},
        {"title": "B", "url": "https://b.example", "content": "b text", "description": ""},
        {"title": None, "url": "https://c.example", "content": None, "description": None},
    ]})

    results = providers.sofya_search("odysseus", count=5, time_filter="week")

    assert results == [
        {"title": "A", "url": "https://a.example", "snippet": "a snippet", "age": "2026-10-01"},
        {"title": "B", "url": "https://b.example", "snippet": "b text", "age": ""},
        {"title": "", "url": "https://c.example", "snippet": "", "age": ""},
    ]
    url, kwargs = calls.posts[0]
    assert url == "https://sofya.co/v1/search"
    assert kwargs["headers"]["Authorization"] == "Bearer ay_live_test"
    assert kwargs["json"] == {
        "query": "odysseus", "max_results": 5, "search_depth": "snippets", "freshness": "week",
    }


def test_sofya_ignores_unknown_time_filter(calls):
    calls.settings["sofya_api_key"] = "k"
    providers.sofya_search("q", count=3, time_filter="decade")
    assert "freshness" not in calls.posts[0][1]["json"]


def test_sofya_without_key_skips_request(calls):
    assert providers.sofya_search("q") == []
    assert calls.posts == []
    assert providers.provider_configured("sofya") is False


def test_sofya_reads_env_key(calls, monkeypatch):
    monkeypatch.setenv("SOFYA_API_KEY", "env-key")
    assert providers.provider_configured("sofya") is True
    providers.sofya_search("q")
    assert calls.posts[0][1]["headers"]["Authorization"] == "Bearer env-key"


def test_sofya_rate_limit_returns_empty(calls):
    calls.settings["sofya_api_key"] = "k"
    calls.response = _Response(status_code=429)
    assert providers.sofya_search("q") == []


@pytest.mark.parametrize("status", [401, 402, 503])
def test_sofya_error_status_raises(calls, status):
    # Like Tavily and Serper: the caller retries once and records the error,
    # so a bad key or empty balance shows as an error, not as "no results".
    calls.settings["sofya_api_key"] = "k"
    calls.response = _Response(status_code=status)
    with pytest.raises(httpx.HTTPStatusError):
        providers.sofya_search("q")


def test_sofya_network_error_returns_empty(calls, monkeypatch):
    calls.settings["sofya_api_key"] = "k"

    def _fail(*_a, **_k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(providers.httpx, "post", _fail)
    assert providers.sofya_search("q") == []


def test_sofya_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr(core, "sofya_search", lambda *a: seen.append(a) or [{"url": "u"}])
    assert core._call_provider("sofya", "q", 4, "day") == [{"url": "u"}]
    assert seen == [("q", 4, "day")]


def _route(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


class _AuthManager:
    def get_username_for_token(self, token):
        return "admin" if token == "admin-session" else None

    def is_admin(self, username):
        return username == "admin"


class _Request(SimpleNamespace):
    def __init__(self, body=None, *, admin=False):
        super().__init__(
            cookies={auth_routes.SESSION_COOKIE: "admin-session"} if admin else {},
            _body=body,
        )

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_sofya_key_saves_and_loads(monkeypatch):
    store = dict(settings_mod.DEFAULT_SETTINGS)
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(store))
    monkeypatch.setattr(auth_routes, "_save_settings", lambda s: (store.clear(), store.update(s)))
    router = auth_routes.setup_auth_routes(_AuthManager())
    set_settings = _route(router, "/api/auth/settings", "POST")
    get_settings = _route(router, "/api/auth/settings", "GET")

    await set_settings(_Request({"search_provider": "sofya", "sofya_api_key": "ay_live_saved"}, admin=True))

    assert store["search_provider"] == "sofya"
    assert store["sofya_api_key"] == "ay_live_saved"
    assert (await get_settings(_Request(admin=True)))["sofya_api_key"] == "ay_live_saved"
    assert (await get_settings(_Request()))["sofya_api_key"] != "ay_live_saved"

    monkeypatch.delenv("SOFYA_API_KEY", raising=False)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: dict(store))
    monkeypatch.setattr(core, "_get_search_settings", lambda: dict(store))
    assert providers._get_provider_key("sofya") == "ay_live_saved"
    assert core.get_search_config()["has_api_key"] is True
