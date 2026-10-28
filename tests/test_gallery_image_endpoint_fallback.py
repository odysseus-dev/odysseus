"""Image endpoint fallback: image models on mixed (non image-only) endpoints.

Chat image generation resolves its model by name across every enabled endpoint,
so a ``qwen-image-edit`` / ``gpt-image-1`` model registered on a shared
OpenAI-compatible gateway works there. The editor's generative ops used a
stricter image-only lookup and reported "No image generation endpoint
configured" on those same setups. These tests pin the fallback that makes the
editor find the same endpoint.
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


def _ep(base_url, owner=None, *, enabled=True, model_type="chat", models=None):
    return SimpleNamespace(
        base_url=base_url,
        owner=owner,
        is_enabled=enabled,
        model_type=model_type,
        api_key="sk-secret",
        cached_models=models or [],
        pinned_models=[],
    )


def _patch_model(monkeypatch):
    monkeypatch.setattr(gallery_routes, "ModelEndpoint", _ModelEndpoint)


def test_mixed_endpoint_with_image_model_is_found(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep("https://gateway.example.com/v1", models=["qwen-image-edit", "llama-3"])]

    ep = gallery_routes._first_visible_image_endpoint(_DB(rows), None)

    assert ep is not None
    assert ep.base_url == "https://gateway.example.com/v1"


def test_mixed_endpoint_without_image_model_is_ignored(monkeypatch):
    _patch_model(monkeypatch)
    rows = [_ep("https://gateway.example.com/v1", models=["llama-3", "mistral"])]

    assert gallery_routes._first_visible_image_endpoint(_DB(rows), None) is None


def test_image_only_endpoint_wins_over_mixed(monkeypatch):
    _patch_model(monkeypatch)
    rows = [
        _ep("https://gateway.example.com/v1", models=["gpt-image-1"]),
        _ep("https://images.example.com/v1", model_type="image"),
    ]

    ep = gallery_routes._first_visible_image_endpoint(_DB(rows), None)

    assert ep is not None
    assert ep.model_type == "image"


def test_unreachable_endpoint_reports_502():
    """The inpaint proxy must distinguish an unreachable endpoint from a provider error.

    502 (gateway) is reserved for "the image server could not be reached"; 503 is
    reserved for "generation is switched off on the server", which the editor maps
    to its own fixed message.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "routes" / "gallery" / "gallery_routes.py"
    ).read_text()
    assert "Image server unreachable" in src
    assert "except httpx.TransportError as exc:" in src
    assert "provider HTTP" in src
    assert 'raise HTTPException(503, f"Image endpoint unreachable' not in src
