"""Owner-scope regression for gallery image-edit endpoint key resolution.

The inpaint/harmonize routes may attach a stored ModelEndpoint api_key when a
caller selects an endpoint URL, or when the route falls back to the first image
endpoint. Both paths must be scoped to endpoints visible to the current user so
one user cannot spend another user's private image endpoint key.
"""

from types import SimpleNamespace

import routes.gallery_routes as gallery_routes


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
    is_enabled = _Column("is_enabled")
    model_type = _Column("model_type")
    owner = _Column("owner")


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates):
        self._rows = [r for r in self._rows if all(p(r) for p in predicates)]
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        assert model is _ModelEndpoint
        return _Query(self._rows)


def _ep(base_url, owner, *, is_enabled=True, model_type="image"):
    return SimpleNamespace(
        base_url=base_url,
        owner=owner,
        is_enabled=is_enabled,
        model_type=model_type,
        api_key=f"key-{owner or 'shared'}",
    )


def _patch_model(monkeypatch):
    monkeypatch.setattr(gallery_routes, "ModelEndpoint", _ModelEndpoint)


URL = "https://img.example.com/v1"


def test_image_endpoint_url_match_rejects_another_owners_private_key(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, "bob")]

    assert gallery_routes._visible_image_endpoint_by_url(_DB(rows), URL, "alice") is None


def test_image_endpoint_url_match_returns_callers_own_key(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, "bob"), _ep(URL, "alice")]

    ep = gallery_routes._visible_image_endpoint_by_url(_DB(rows), URL, "alice")

    assert ep is not None
    assert ep.owner == "alice"
    assert ep.api_key == "key-alice"


def test_image_endpoint_url_match_allows_legacy_shared_row(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, None)]

    ep = gallery_routes._visible_image_endpoint_by_url(_DB(rows), URL, "alice")

    assert ep is not None
    assert ep.owner is None


def test_image_endpoint_url_match_requires_enabled_image_endpoint(monkeypatch):
    _patch_model(monkeypatch)
    rows = [
        _ep(URL, "alice", is_enabled=False),
        _ep(URL, "alice", model_type="chat"),
    ]

    assert gallery_routes._visible_image_endpoint_by_url(_DB(rows), URL, "alice") is None


def test_image_endpoint_url_normalization_is_exact(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep("http://localhost:8000/v1", "alice")]

    assert gallery_routes._visible_image_endpoint_by_url(
        _DB(rows),
        "http://localhost:8000",
        "alice",
    ) is not None
    assert gallery_routes._visible_image_endpoint_by_url(
        _DB(rows),
        "http://localhost:8000/v11",
        "alice",
    ) is None


def test_image_endpoint_fallback_never_picks_another_owners_endpoint(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep("https://bob.example/v1", "bob"), _ep("https://shared.example/v1", None)]

    ep = gallery_routes._first_visible_image_endpoint(_DB(rows), "alice")

    assert ep is not None
    assert ep.owner is None


def test_image_endpoint_fallback_returns_none_when_only_others_endpoints(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep("https://bob.example/v1", "bob"), _ep("https://carol.example/v1", "carol")]

    assert gallery_routes._first_visible_image_endpoint(_DB(rows), "alice") is None


def test_null_owner_is_legacy_single_user_noop(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, "bob")]

    ep = gallery_routes._visible_image_endpoint_by_url(_DB(rows), URL, None)

    assert ep is not None
    assert ep.owner == "bob"
