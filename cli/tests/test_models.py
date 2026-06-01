"""Unit tests for the model-listing module (no live server required)."""

import json
import os
import sys
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from odysseus_cli import models  # noqa: E402


# ── models_url normalization ───────────────────────────────────────────────
def test_models_url_from_v1():
    assert models.models_url("http://localhost:11434/v1") == "http://localhost:11434/v1/models"


def test_models_url_from_bare_host():
    assert models.models_url("http://localhost:11434") == "http://localhost:11434/v1/models"


def test_models_url_from_chat_completions():
    url = "http://h:8000/v1/chat/completions"
    assert models.models_url(url) == "http://h:8000/v1/models"


# ── list_models parsing ────────────────────────────────────────────────────
def _fake_urlopen(payload):
    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(payload).encode("utf-8")
    return lambda req, timeout=5.0: _Resp()


def test_list_models_sorted_ids(monkeypatch):
    payload = {"object": "list", "data": [
        {"id": "qwen2.5-coder:7b"}, {"id": "all-minilm:latest"},
        {"id": "llama3.2:3b"},
    ]}
    monkeypatch.setattr(models.urllib.request, "urlopen", _fake_urlopen(payload))
    out = models.list_models("http://localhost:11434/v1")
    assert out == ["all-minilm:latest", "llama3.2:3b", "qwen2.5-coder:7b"]


def test_list_models_handles_failure(monkeypatch):
    def _boom(req, timeout=5.0):
        raise OSError("connection refused")
    monkeypatch.setattr(models.urllib.request, "urlopen", _boom)
    assert models.list_models("http://localhost:11434/v1") == []


def test_list_models_handles_bad_shape(monkeypatch):
    monkeypatch.setattr(models.urllib.request, "urlopen", _fake_urlopen({"oops": 1}))
    assert models.list_models("http://localhost:11434/v1") == []
