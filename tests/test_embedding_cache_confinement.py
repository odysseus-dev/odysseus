import sys
import types

import pytest
from fastapi import HTTPException

import routes.embedding_routes as embedding_routes


def _install_fastembed_stub(monkeypatch):
    fastembed = types.ModuleType("fastembed")

    class TextEmbedding:
        @staticmethod
        def list_supported_models():
            return [{"model": "test-model", "sources": {"hf": "org/test-model"}}]

    fastembed.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)


def _route_endpoint(path: str, method: str):
    router = embedding_routes.setup_embedding_routes()
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_model_cache_path_resolves_under_cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding_routes, "_cache_dir", lambda: str(tmp_path / "cache"))

    path = embedding_routes._model_cache_path("org/test-model")

    assert path == (tmp_path / "cache" / "models--org--test-model").resolve()


def test_model_cache_path_rejects_top_level_symlink_escape(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    outside = tmp_path / "outside"
    cache.mkdir()
    outside.mkdir()
    monkeypatch.setattr(embedding_routes, "_cache_dir", lambda: str(cache))
    link = cache / "models--org--test-model"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError):
        embedding_routes._model_cache_path("org/test-model")
    assert embedding_routes._is_downloaded("org/test-model") is False


def test_delete_model_rejects_symlink_cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    outside = tmp_path / "outside"
    cache.mkdir()
    outside.mkdir()
    (outside / "keep.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(embedding_routes, "_cache_dir", lambda: str(cache))
    monkeypatch.setattr(embedding_routes, "_active_model", lambda: "other-model")
    _install_fastembed_stub(monkeypatch)
    link = cache / "models--org--test-model"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    delete_model = _route_endpoint("/api/embeddings/models/{model_name:path}", "DELETE")

    with pytest.raises(HTTPException) as exc:
        delete_model("test-model")

    assert exc.value.status_code == 400
    assert (outside / "keep.txt").exists()
