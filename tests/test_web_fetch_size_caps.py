"""web_fetch download budgets (#3812).

MAX_OUTPUT_CHARS only trims what the agent sees; these caps bound what the
server downloads, parses, and caches. Soft cap by default with a truncation
notice, per-call override clamped to the hard cap, and a pre-buffer refusal
when Content-Length already exceeds the hard ceiling.
"""
import json
from contextlib import contextmanager

import pytest

from src.constants import WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES
from services.search import content as content_mod


class _FakeStream:
    """Stands in for the httpx.stream(...) context manager."""

    def __init__(self, body: bytes, content_type="text/plain", content_length=None,
                 status_code=200, chunk=8192):
        self._body = body
        self._chunk = chunk
        self.status_code = status_code
        self.encoding = "utf-8"
        self.url = "https://example.com/x"
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.body_reads = 0

    def iter_bytes(self):
        for i in range(0, len(self._body), self._chunk):
            self.body_reads += 1
            yield self._body[i:i + self._chunk]


@pytest.fixture
def no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(content_mod, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content_mod, "_cache_result", lambda *a, **k: None)
    monkeypatch.setattr(content_mod, "_public_http_url", lambda u: True)


def _patch_stream(monkeypatch, fake):
    @contextmanager
    def fake_stream(method, url, **kwargs):
        yield fake
    monkeypatch.setattr(content_mod.httpx, "stream", fake_stream)
    return fake


def test_body_under_cap_is_untouched(monkeypatch, no_cache):
    _patch_stream(monkeypatch, _FakeStream(b"hello world"))
    r = content_mod.fetch_webpage_content("https://example.com/a.txt")
    assert r["success"] is True
    assert r["content"] == "hello world"
    assert r["truncated"] is False
    assert r["fetched_bytes"] == len(b"hello world")


def test_body_over_soft_cap_truncates_with_flags(monkeypatch, no_cache):
    body = b"x" * (WEB_FETCH_SOFT_MAX_BYTES + 50_000)
    _patch_stream(monkeypatch, _FakeStream(body, content_length=len(body)))
    r = content_mod.fetch_webpage_content("https://example.com/big.txt")
    assert r["truncated"] is True
    assert r["fetched_bytes"] == WEB_FETCH_SOFT_MAX_BYTES
    assert r["total_bytes"] == len(body)
    assert len(r["content"]) == WEB_FETCH_SOFT_MAX_BYTES


def test_max_bytes_override_raises_budget(monkeypatch, no_cache):
    body = b"y" * (WEB_FETCH_SOFT_MAX_BYTES + 50_000)
    _patch_stream(monkeypatch, _FakeStream(body))
    r = content_mod.fetch_webpage_content(
        "https://example.com/big.txt", max_bytes=len(body) + 1
    )
    assert r["truncated"] is False
    assert r["fetched_bytes"] == len(body)


def test_override_is_clamped_to_hard_cap(monkeypatch, no_cache):
    # Ask for more than the ceiling; the effective budget must be the ceiling.
    fake = _patch_stream(monkeypatch, _FakeStream(b"z" * 10, chunk=4))
    r = content_mod.fetch_webpage_content(
        "https://example.com/a.txt", max_bytes=WEB_FETCH_HARD_MAX_BYTES * 10
    )
    assert r["success"] is True
    # The clamp itself: effective cap recorded in the cache key path is the
    # hard cap, and a declared body over the ceiling is refused regardless.
    big = _FakeStream(b"", content_length=WEB_FETCH_HARD_MAX_BYTES + 1)
    _patch_stream(monkeypatch, big)
    r = content_mod.fetch_webpage_content(
        "https://example.com/huge.bin", max_bytes=WEB_FETCH_HARD_MAX_BYTES * 10
    )
    assert r["success"] is False
    assert "TooLarge" in r["error"]
    assert big.body_reads == 0  # refused before buffering


def test_declared_over_hard_cap_refused_before_buffering(monkeypatch, no_cache):
    fake = _FakeStream(b"irrelevant", content_length=WEB_FETCH_HARD_MAX_BYTES + 1)
    _patch_stream(monkeypatch, fake)
    r = content_mod.fetch_webpage_content("https://example.com/huge.iso")
    assert r["success"] is False
    assert "TooLarge" in r["error"]
    assert fake.body_reads == 0


def test_truncated_pdf_is_an_error_not_garbage(monkeypatch, no_cache):
    body = b"%PDF-1.4 " + b"p" * (WEB_FETCH_SOFT_MAX_BYTES + 10)
    _patch_stream(monkeypatch, _FakeStream(body, content_type="application/pdf"))
    r = content_mod.fetch_webpage_content("https://example.com/big.pdf")
    assert r["success"] is False
    assert "TooLarge" in r["error"]


def test_fetch_requests_identity_encoding(monkeypatch, no_cache):
    # Compressed responses can decode to far more than Content-Length, so the
    # streamed cap and the hard-cap preflight are only honest when we refuse
    # transfer compression. Pin that the fetch advertises identity, not gzip.
    seen = {}

    @contextmanager
    def fake_stream(method, url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        yield _FakeStream(b"hello")
    monkeypatch.setattr(content_mod.httpx, "stream", fake_stream)

    content_mod.fetch_webpage_content("https://example.com/a.txt")
    assert seen["headers"].get("Accept-Encoding") == "identity"


def test_rejects_compressed_response_that_ignored_identity(monkeypatch, no_cache):
    # We request Accept-Encoding: identity, but a server can ignore it and send
    # gzip anyway. httpx would decode it, so a tiny compressed body could balloon
    # past the cap in one decoded chunk. Refuse before reading the body.
    fake = _FakeStream(b"x" * 5000, content_length=40)
    fake.headers["content-encoding"] = "gzip"
    _patch_stream(monkeypatch, fake)
    r = content_mod.fetch_webpage_content("https://example.com/a.txt")
    assert r["success"] is False
    assert "Content-Encoding" in r["error"] or "compressed" in r["error"]
    assert fake.body_reads == 0  # refused before decoding any body


def test_oversized_title_does_not_hide_partial_notice(monkeypatch):
    # The partial-content notice is the PR's core contract; an untrusted,
    # oversized page title must not push it past MAX_OUTPUT_CHARS.
    import asyncio
    from src.agent_tools.web_tools import WebFetchTool
    from src.constants import MAX_OUTPUT_CHARS

    def fake_fetch(url, timeout=10, max_bytes=None):
        return {
            "content": "partial body",
            "title": "T" * (MAX_OUTPUT_CHARS + 5_000),
            "error": "",
            "truncated": True,
            "fetched_bytes": WEB_FETCH_SOFT_MAX_BYTES,
            "total_bytes": 9_000_000,
        }

    import src.search.content as alias_mod
    monkeypatch.setattr(alias_mod, "fetch_webpage_content", fake_fetch)

    out = asyncio.run(WebFetchTool().execute(
        json.dumps({"url": "https://example.com/big.txt"}), ctx={}
    ))
    assert out["exit_code"] == 0
    assert out["output"].startswith("[partial content:")
    assert '"full": true' in out["output"]


def test_tool_layer_emits_partial_notice_and_parses_full(monkeypatch):
    import asyncio
    from src.agent_tools.web_tools import WebFetchTool

    calls = {}

    def fake_fetch(url, timeout=10, max_bytes=None):
        calls["max_bytes"] = max_bytes
        return {
            "content": "partial body",
            "title": "Big File",
            "error": "",
            "truncated": True,
            "fetched_bytes": WEB_FETCH_SOFT_MAX_BYTES,
            "total_bytes": 5_000_000,
        }

    import src.search.content as alias_mod
    monkeypatch.setattr(alias_mod, "fetch_webpage_content", fake_fetch)

    out = asyncio.run(WebFetchTool().execute(
        json.dumps({"url": "https://example.com/big.txt"}), ctx={}
    ))
    assert out["exit_code"] == 0
    assert "[partial content:" in out["output"]
    assert '"full": true' in out["output"]
    assert calls["max_bytes"] is None

    asyncio.run(WebFetchTool().execute(
        json.dumps({"url": "https://example.com/big.txt", "full": True}), ctx={}
    ))
    assert calls["max_bytes"] == WEB_FETCH_HARD_MAX_BYTES
