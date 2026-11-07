"""Regression tests for the real resolve_endpoint() fallback chain."""

import json
from types import SimpleNamespace

import pytest

import src.endpoint_resolver as endpoint_resolver
from src.endpoint_resolver import (
    endpoint_cost_tracked,
    resolve_endpoint,
    resolve_endpoint_by_id,
    resolve_fallback_entries,
    resolve_fallback_entries_with_descriptors,
)


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

    def all(self):
        return list(self.rows)


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
        name=f"Endpoint {ep_id}",
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


def test_task_session_fallback_wins_before_default_when_task_and_utility_unset(monkeypatch):
    settings = {
        "task_endpoint_id": "",
        "task_model": "",
        "utility_endpoint_id": "",
        "utility_model": "",
        "default_endpoint_id": "default",
        "default_model": "default-chat",
    }
    fallback = ("https://session.example/chat", "session-chat", {"X-Test": "session"})
    _install_resolver_fakes(monkeypatch, settings, [_endpoint("default", "default-chat")])

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


def test_exact_fallback_drops_hidden_model_instead_of_substituting(monkeypatch):
    endpoint = SimpleNamespace(
        id="fallback",
        base_url="https://fallback.example/v1",
        api_key="key-fallback",
        cached_models=json.dumps(["chosen-hidden", "different-live"]),
        hidden_models=json.dumps(["chosen-hidden"]),
        is_enabled=True,
    )
    _install_resolver_fakes(monkeypatch, {}, [endpoint])

    assert resolve_endpoint_by_id(
        "fallback",
        "chosen-hidden",
        require_exact_model=True,
    ) is None
    assert resolve_endpoint_by_id("fallback", "chosen-hidden")[1] == "different-live"


def test_exact_fallback_drops_known_missing_model(monkeypatch):
    _install_resolver_fakes(monkeypatch, {}, [_endpoint("fallback", "known-live")])

    assert resolve_endpoint_by_id(
        "fallback",
        "unlisted-model",
        require_exact_model=True,
    ) is None


def test_fallback_entry_resolution_preserves_credential_distinct_endpoints(monkeypatch):
    seen = []

    def fake_resolve(ep_id, model, owner=None, *, require_exact_model=False):
        seen.append((ep_id, model, owner, require_exact_model))
        return (
            "https://provider.example/v1/chat/completions",
            model,
            {"Authorization": f"Bearer {ep_id}"},
        )

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_by_id", fake_resolve)
    entries = [
        {"endpoint_id": "key-one", "model": "same-model"},
        {"endpoint_id": "key-two", "model": "same-model"},
    ]

    assert resolve_fallback_entries(
        entries,
        owner="alice",
        require_exact_model=True,
    ) == [
        ("https://provider.example/v1/chat/completions", "same-model", {"Authorization": "Bearer key-one"}),
        ("https://provider.example/v1/chat/completions", "same-model", {"Authorization": "Bearer key-two"}),
    ]
    assert seen == [
        ("key-one", "same-model", "alice", True),
        ("key-two", "same-model", "alice", True),
    ]


def test_descriptor_resolution_preserves_safe_endpoint_identity(monkeypatch):
    _install_resolver_fakes(monkeypatch, {}, [_endpoint("backup", "backup-model")])

    routes = resolve_fallback_entries_with_descriptors(
        [{"endpoint_id": "backup", "model": "backup-model"}],
        require_exact_model=True,
    )

    assert routes == [(
        (
            "https://backup.example/v1/chat/completions",
            "backup-model",
            {"Authorization": "Bearer key-backup"},
        ),
        {
            "endpoint_id": "backup",
            "endpoint_label": "Endpoint backup",
            "endpoint_cost_tracked": True,
        },
    )]


def test_exact_id_descriptor_wins_when_routes_are_identical(monkeypatch):
    first = _endpoint("account-one", "same-model")
    second = _endpoint("account-two", "same-model")
    for endpoint in (first, second):
        endpoint.base_url = "https://provider.example/v1"
        endpoint.api_key = "shared-key"
    _install_resolver_fakes(monkeypatch, {}, [first, second])

    import src.auth_helpers as auth_helpers

    seen_owners = []

    def scoped(query, model_cls, owner, *, include_shared=True):
        seen_owners.append(owner)
        return query

    monkeypatch.setattr(auth_helpers, "owner_filter", scoped)
    resolver = getattr(endpoint_resolver, "resolve_route_descriptor_by_id", None)

    assert resolver is not None
    assert resolver(
        "account-two",
        "https://provider.example/v1/chat/completions",
        "same-model",
        {"Authorization": "Bearer shared-key"},
        owner="alice",
    ) == {
        "endpoint_id": "account-two",
        "endpoint_label": "Endpoint account-two",
        "endpoint_cost_tracked": True,
    }
    assert seen_owners == ["alice"]


def test_endpoint_cost_tracking_is_non_secret_route_classification():
    assert endpoint_cost_tracked("http://localhost:11434/v1") is False
    assert endpoint_cost_tracked("http://model-service:8000/v1") is False
    assert endpoint_cost_tracked("http://192.168.1.20:8000/v1") is False
    assert endpoint_cost_tracked("https://chatgpt.com/backend-api/codex") is False
    assert endpoint_cost_tracked("https://api.example.com/v1") is True
    assert endpoint_cost_tracked("http://192.168.1.20:8000/v1", "api") is True
    assert endpoint_cost_tracked("https://api.example.com/v1", "local") is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://[2606:4700:4700::1111]/v1", True),
        ("http://169.254.10.20:8000/v1", False),
    ],
)
def test_endpoint_cost_tracking_classifies_public_ipv6_and_link_local_ipv4(url, expected):
    assert endpoint_cost_tracked(url) is expected
