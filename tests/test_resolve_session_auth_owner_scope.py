"""Owner-scope regression for chat_helpers.resolve_session_auth.

resolve_session_auth() runs in the chat_stream path (after _verify_session_owner
confirms the caller owns the session). When the session has no auth headers it
matches a ModelEndpoint by the session endpoint_url's host substring and copies
that row's *decrypted* api_key into the session headers — and PERSISTS it. The
match must be owner-scoped (the session owner's own rows + legacy null-owner
shared rows) so a user can't point a session they own at another user's endpoint
host and silently adopt that owner's api_key. Mirrors the session/research/compare
owner-scope fixes.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

if "core.database" not in sys.modules:
    sys.modules["core.database"] = types.ModuleType("core.database")
_cd = sys.modules["core.database"]
_cd.Base = MagicMock()
for _name in (
    "Session", "ChatMessage", "Document", "GalleryImage", "SessionLocal",
    "ModelEndpoint",
):
    if not hasattr(_cd, _name):
        setattr(_cd, _name, MagicMock())

from routes.chat_helpers import _owned_endpoint_by_domain  # noqa: E402


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

    def contains(self, needle):
        return _Predicate(lambda row: needle in (getattr(row, self.name) or ""))


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


def _resolve(rows, domain, owner):
    # The helper references chat_helpers' module-level ModelEndpoint (bound at
    # import via `from core.database import ModelEndpoint`), so patch it there.
    import routes.chat_helpers as _ch
    _ch.ModelEndpoint = _ModelEndpoint
    return _owned_endpoint_by_domain(_DB(rows), domain, owner)


HOST = "api.example.com"


def test_rejects_another_owners_private_endpoint():
    # bob's endpoint on this host; alice (session owner) must not adopt its key.
    rows = [_ep(f"https://{HOST}/v1", "bob")]
    assert _resolve(rows, HOST, "alice") is None


def test_returns_session_owners_own_endpoint():
    rows = [_ep(f"https://{HOST}/v1", "bob"), _ep(f"https://{HOST}/v1", "alice")]
    ep = _resolve(rows, HOST, "alice")
    assert ep is not None and ep.owner == "alice"


def test_allows_legacy_null_owner_shared_row():
    rows = [_ep(f"https://{HOST}/v1", None)]
    ep = _resolve(rows, HOST, "alice")
    assert ep is not None and ep.owner is None


def test_no_host_match_returns_none():
    rows = [_ep("https://other.host/v1", "alice")]
    assert _resolve(rows, HOST, "alice") is None


def test_null_owner_is_legacy_single_user_noop():
    rows = [_ep(f"https://{HOST}/v1", "bob")]
    ep = _resolve(rows, HOST, None)
    assert ep is not None and ep.owner == "bob"
