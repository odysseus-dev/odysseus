import sys
import types

import pytest

from src import embeddings


def _install_fastembed(monkeypatch):
    calls = []
    module = types.ModuleType("fastembed")

    class TextEmbedding:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    module.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return calls


def test_fastembed_threads_default_is_unchanged(monkeypatch, tmp_path):
    calls = _install_fastembed(monkeypatch)
    monkeypatch.delenv("FASTEMBED_THREADS", raising=False)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(tmp_path))

    embeddings.FastEmbedClient(model="test-model")

    assert calls == [{"model_name": "test-model", "cache_dir": str(tmp_path)}]


def test_fastembed_threads_can_be_bounded(monkeypatch, tmp_path):
    calls = _install_fastembed(monkeypatch)
    monkeypatch.setenv("FASTEMBED_THREADS", "2")
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(tmp_path))

    embeddings.FastEmbedClient(model="test-model")

    assert calls == [
        {"model_name": "test-model", "cache_dir": str(tmp_path), "threads": 2}
    ]


@pytest.mark.parametrize("value", ["0", "257", "not-a-number"])
def test_fastembed_threads_rejects_invalid_values(monkeypatch, tmp_path, value):
    _install_fastembed(monkeypatch)
    monkeypatch.setenv("FASTEMBED_THREADS", value)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="FASTEMBED_THREADS"):
        embeddings.FastEmbedClient(model="test-model")
