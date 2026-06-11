"""Owner-scope regression for gallery image endpoint selection.

The image editor/upscale proxies select ``ModelEndpoint`` rows and may copy the
row's stored ``api_key`` for OpenAI-compatible image endpoints. That lookup must
only consider endpoints visible to the caller, otherwise users sharing the same
base URL can borrow another account's private image API key.
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
    model_type = _Column("model_type")
    is_enabled = _Column("is_enabled")
    owner = _Column("owner")


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates):
        self._rows = [row for row in self._rows if all(pred(row) for pred in predicates)]
        return self

    def all(self):
        return list(self._rows)


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        assert model is _ModelEndpoint
        return _Query(self._rows)


def _ep(base_url, owner, *, enabled=True, model_type="image", api_key="sk-secret"):
    return SimpleNamespace(
        base_url=base_url,
        owner=owner,
        is_enabled=enabled,
        model_type=model_type,
        api_key=api_key,
    )


def _patch_model(monkeypatch):
    monkeypatch.setattr(gallery_routes, "ModelEndpoint", _ModelEndpoint)


URL = "https://api.example.com/v1"


def test_first_visible_image_endpoint_rejects_another_owner(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, "bob")]

    assert gallery_routes._first_visible_image_endpoint(_DB(rows), "alice") is None


def test_first_visible_image_endpoint_prefers_callers_own_row(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, None, api_key="shared"), _ep(URL, "alice", api_key="own")]

    ep = gallery_routes._first_visible_image_endpoint(_DB(rows), "alice")

    assert ep is not None
    assert ep.owner == "alice"
    assert ep.api_key == "own"


def test_visible_image_endpoint_for_base_rejects_same_url_other_owner(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, "bob")]

    assert gallery_routes._visible_image_endpoint_for_base(_DB(rows), URL, "alice") is None


def test_visible_image_endpoint_for_base_allows_shared_or_own(monkeypatch):
    _patch_model(monkeypatch)
    rows = [
        _ep("https://other.example/v1", "alice"),
        _ep(URL, None, api_key="shared"),
        _ep(URL, "alice", api_key="own"),
    ]

    ep = gallery_routes._visible_image_endpoint_for_base(_DB(rows), "https://api.example.com", "alice")

    assert ep is not None
    assert ep.owner == "alice"
    assert ep.api_key == "own"
    assert ep.base_url == URL


def test_image_endpoint_owner_filter_is_noop_in_single_user_mode(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep(URL, "bob")]

    ep = gallery_routes._visible_image_endpoint_for_base(_DB(rows), URL, None)

    assert ep is not None
    assert ep.owner == "bob"
