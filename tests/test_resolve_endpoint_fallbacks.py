"""Regression tests for the real resolve_endpoint() fallback chain."""

import json
from types import SimpleNamespace

import src.endpoint_resolver as endpoint_resolver
from src.endpoint_resolver import resolve_endpoint


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)


class _FakeModelEndpoint:
    id = _FakeColumn("id")
    is_enabled = _FakeColumn("is_enabled")


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for condition in conditions:
            if isinstance(condition, tuple) and condition[0] == "eq":
                _, field, value = condition
                self.rows = [row for row in self.rows if getattr(row, field) == value]
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _FakeQuery(self.rows)

    def close(self):
        pass


def _endpoint(ep_id, model, *, hidden=None):
    return SimpleNamespace(
        id=ep_id,
        base_url=f"https://{ep_id}.example/v1",
        api_key=f"key-{ep_id}",
        cached_models=json.dumps([model]),
        hidden_models=json.dumps(hidden or []),
        is_enabled=True,
    )


def _install_resolver_fakes(monkeypatch, settings, endpoints):
    import src.settings as settings_mod

    monkeypatch.setattr(settings_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(
        settings_mod,
        "get_user_setting",
        lambda key, owner="", default=None: settings.get(key, default),
    )
    monkeypatch.setattr(endpoint_resolver, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", lambda: _FakeDb(endpoints))
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)


def test_utility_uses_default_when_utility_endpoint_unset(monkeypatch):
    settings = {
        "utility_endpoint_id": "",
        "utility_model": "",
        "default_endpoint_id": "default",
        "default_model": "default-chat",
    }
    _install_resolver_fakes(monkeypatch, settings, [_endpoint("default", "default-chat")])

    url, model, headers = resolve_endpoint("utility")

    assert url == "https://default.example/v1/chat/completions"
    assert model == "default-chat"
    assert headers == {"Authorization": "Bearer key-default"}


def test_task_uses_utility_when_task_endpoint_unset(monkeypatch):
    settings = {
        "task_endpoint_id": "",
        "task_model": "",
        "utility_endpoint_id": "utility",
        "utility_model": "utility-chat",
        "default_endpoint_id": "default",
        "default_model": "default-chat",
    }
    _install_resolver_fakes(
        monkeypatch,
        settings,
        [_endpoint("utility", "utility-chat"), _endpoint("default", "default-chat")],
    )

    url, model, headers = resolve_endpoint("task")

    assert url == "https://utility.example/v1/chat/completions"
    assert model == "utility-chat"
    assert headers == {"Authorization": "Bearer key-utility"}


def test_research_uses_default_when_research_and_utility_unset(monkeypatch):
    settings = {
        "research_endpoint_id": "",
        "research_model": "",
        "utility_endpoint_id": "",
        "utility_model": "",
        "default_endpoint_id": "default",
        "default_model": "default-chat",
    }
    _install_resolver_fakes(monkeypatch, settings, [_endpoint("default", "default-chat")])

    url, model, headers = resolve_endpoint("research")

    assert url == "https://default.example/v1/chat/completions"
    assert model == "default-chat"
    assert headers == {"Authorization": "Bearer key-default"}


def test_returns_explicit_fallback_when_no_endpoint_id_configured(monkeypatch):
    settings = {
        "task_endpoint_id": "",
        "task_model": "",
        "utility_endpoint_id": "",
        "utility_model": "",
        "default_endpoint_id": "",
        "default_model": "",
    }
    fallback = ("https://fallback.example/chat", "fallback-chat", {"X-Test": "fallback"})
    _install_resolver_fakes(monkeypatch, settings, [])

    assert resolve_endpoint(
        "task",
        fallback_url=fallback[0],
        fallback_model=fallback[1],
        fallback_headers=fallback[2],
    ) == fallback


def test_hidden_configured_model_selects_first_enabled_chat_model(monkeypatch):
    settings = {
        "default_endpoint_id": "default",
        "default_model": "hidden-chat",
    }
    endpoint = SimpleNamespace(
        id="default",
        base_url="https://default.example/v1",
        api_key="key-default",
        cached_models=json.dumps([
            "hidden-chat",
            "text-embedding-3-small",
            "enabled-chat",
        ]),
        hidden_models=json.dumps(["hidden-chat"]),
        is_enabled=True,
    )
    _install_resolver_fakes(monkeypatch, settings, [endpoint])

    url, model, headers = resolve_endpoint("default")

    assert url == "https://default.example/v1/chat/completions"
    assert model == "enabled-chat"
    assert headers == {"Authorization": "Bearer key-default"}
