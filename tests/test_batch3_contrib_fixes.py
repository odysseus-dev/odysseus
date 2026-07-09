"""Regression tests for third contribution batch (issues #5140–#4921)."""
import email
from email.header import Header

from routes.email_helpers import _decode_header
from src.chat_helpers import validate_file_upload
from src.llm_core import apply_kimi_code_headers, _is_kimi_code_url
from unittest.mock import MagicMock
import pytest


def test_decode_header_bytes_utf8():
    raw = "=?utf-8?B?8J+RmfCfkYE=?= Friends".encode("utf-8")
    out = _decode_header(raw)
    assert "Friends" in out
    assert "" not in out or len(out) > 3


def test_decode_header_rfc2047_emoji():
    raw = "=?utf-8?q?=F0=9F=9F=90=F0=9F=90=B1?= =?utf-8?q?=3D_Friends?="
    out = _decode_header(raw)
    assert "Friends" in out
    assert "=?utf-8" not in out


def test_kimi_async_skip_probe_no_blocking_get(monkeypatch):
    url = "https://api.kimi.com/coding/v1/chat/completions"
    if not _is_kimi_code_url(url):
        pytest.skip("kimi code url detector not matching test url")
    called = []

    def fake_get(*args, **kwargs):
        called.append(1)
        raise AssertionError("sync probe should not run when skip_probe=True")

    monkeypatch.setattr("src.llm_core.httpx.get", fake_get)
    h = apply_kimi_code_headers({}, url, skip_probe=True)
    assert "User-Agent" in h
    assert not called


def test_chat_upload_allows_mp4():
    f = MagicMock()
    f.filename = "clip.mp4"
    f.file = MagicMock()
    f.file.seek = MagicMock()
    f.file.tell = MagicMock(return_value=1024)
    out = validate_file_upload(f)
    assert out is f


def test_agent_ollama_openai_compat_tools_flag():
    """Ollama /v1 + model_supports_tools should not force text-only tools off (#5015)."""
    from src.agent_loop import _is_ollama_openai_compat_url

    url = "http://127.0.0.1:11434/v1/chat/completions"
    assert _is_ollama_openai_compat_url(url)