"""Owner-scope regression for /api/compare/start endpoint-key resolution.

start_comparison() takes caller-supplied endpoint URLs (endpoint_a/endpoint_b),
matches a ModelEndpoint by base_url, and copies that row's *decrypted* api_key
into the caller-owned [CMP] session's headers — which then drive that session's
/api/chat_stream calls. The match must be owner-scoped (the caller's own rows +
legacy null-owner shared rows) so a user can't mint a comparison bound to
ANOTHER user's private endpoint and spend their api_key / reach their base_url.
Mirrors the session `_owned_endpoint` and research `_owned_enabled_endpoint`
fixes.
"""

from types import SimpleNamespace

import core.database
from routes.compare_routes import _owned_endpoint_by_url


class _Predicate:
    def __init__(self, check):
        self._check = check

    def __call__(self, row):
        return self._check(row)

    def __or__(self, other):
        return _Predicate(lambda row: self(row) or other(row))


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return _Predicate(lambda row: getattr(row, self.name) == value)


class _ModelEndpoint:
    base_url = _Column("base_url")
    owner = _Column("owner")


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates):
        self._rows = [r for r in self._rows if all(p(r) for p in predicates)]
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        assert model is _ModelEndpoint
        return _Query(self._rows)


def _ep(base_url, owner):
    return SimpleNamespace(base_url=base_url, owner=owner, api_key="sk-secret")


def _resolve(monkeypatch, rows, base_url, owner):
    monkeypatch.setattr(core.database, "ModelEndpoint", _ModelEndpoint)
    return _owned_endpoint_by_url(_DB(rows), base_url, owner)


URL = "https://api.example.com/v1"


def test_rejects_another_owners_private_endpoint(monkeypatch):
    # bob owns the only endpoint at URL; alice supplying that URL gets None
    # → no headers, no key copied into her comparison session.
    rows = [_ep(URL, "bob")]
    assert _resolve(monkeypatch, rows, URL, "alice") is None


def test_returns_callers_own_endpoint(monkeypatch):
    rows = [_ep(URL, "bob"), _ep(URL, "alice")]
    ep = _resolve(monkeypatch, rows, URL, "alice")
    assert ep is not None and ep.owner == "alice"


def test_allows_legacy_null_owner_shared_row(monkeypatch):
    rows = [_ep(URL, None)]
    ep = _resolve(monkeypatch, rows, URL, "alice")
    assert ep is not None and ep.owner is None


def test_no_match_returns_none(monkeypatch):
    rows = [_ep("https://other.example/v1", "alice")]
    assert _resolve(monkeypatch, rows, URL, "alice") is None


def test_null_owner_is_legacy_single_user_noop(monkeypatch):
    # Single-user / unresolved owner: owner_filter no-op, exact URL match wins.
    rows = [_ep(URL, "bob")]
    ep = _resolve(monkeypatch, rows, URL, None)
    assert ep is not None and ep.owner == "bob"
