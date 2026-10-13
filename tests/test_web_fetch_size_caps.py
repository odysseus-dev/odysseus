"""web_fetch download budgets (#3812).

MAX_OUTPUT_CHARS only trims what the agent sees; these caps bound what the
server downloads, parses, and caches. Soft cap by default with a truncation
notice, per-call override clamped to the hard cap, and a pre-buffer refusal
when Content-Length already exceeds the hard ceiling.
"""
import json
import ipaddress
from contextlib import contextmanager

import pytest

from src.constants import WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES
from services.search import content as content_mod

import pytest as _pytest_for_client_stream_compat


@_pytest_for_client_stream_compat.fixture(autouse=True)
def _client_stream_compat_for_pinned_fetch(monkeypatch):
    """Adapt old size-cap tests to the current pinned Client.stream path.

    These tests monkeypatch httpx.stream(...) to return fake responses. The
    production fetcher now uses httpx.Client(...).stream(...) so it can pass a
    pinned transport. When a test has replaced httpx.stream, route Client.stream
    through that fake. When it has not, fall back to a real Client so unrelated
    behavior in this file is not changed.
    """
    import httpx

    real_client_cls = httpx.Client
    original_stream = httpx.stream

    class _ClientProxy:
        def __init__(self, *args, **kwargs):
            self._args = args
            self._kwargs = kwargs
            self._real_cm = None
            self._real_client = None

        def __enter__(self):
            if httpx.stream is original_stream:
                self._real_cm = real_client_cls(*self._args, **self._kwargs)
                self._real_client = self._real_cm.__enter__()
                return self._real_client
            return self

        def __exit__(self, *args):
            if self._real_cm is not None:
                return self._real_cm.__exit__(*args)
            return False

        def stream(self, method, url):
            if self._real_client is not None:
                return self._real_client.stream(method, url)

            kwargs = {
                "headers": self._kwargs.get("headers"),
                "timeout": self._kwargs.get("timeout"),
                "follow_redirects": self._kwargs.get("follow_redirects"),
            }
            return httpx.stream(method, url, **kwargs)

    monkeypatch.setattr(httpx, "Client", _ClientProxy)



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
    monkeypatch.setattr(
        content_mod,
        "_resolve_public_ips",
        lambda _url: [ipaddress.ip_address("93.184.216.34")],
    )


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


def test_atom_xml_api_response_is_preserved_as_readable_evidence(monkeypatch, no_cache):
    body = b"<?xml version='1.0'?><feed><entry><title>Daily paper</title></entry></feed>"
    _patch_stream(monkeypatch, _FakeStream(body, content_type="application/atom+xml"))

    result = content_mod.fetch_webpage_content("https://example.com/api/feed")

    assert result["success"] is True
    assert "<title>Daily paper</title>" in result["content"]


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
    assert r["content"] == ""
    assert r["error"]


def test_pdf_fetch_falls_back_to_pypdf_when_pdfminer_missing(monkeypatch, no_cache):
    class _FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage("Setagaya burnable garbage rules")]

    monkeypatch.setattr(content_mod, "pdf_extract_text", None)
    monkeypatch.setattr(content_mod, "PdfReader", _FakeReader)
    _patch_stream(monkeypatch, _FakeStream(b"%PDF-1.4 fake", content_type="application/pdf"))

    r = content_mod.fetch_webpage_content("https://example.com/rules.pdf")

    assert r["success"] is True
    assert "Setagaya burnable garbage rules" in r["content"]


def test_pdf_truncated_by_decoded_soft_cap_retries_full_budget(monkeypatch, no_cache):
    class _FakePage:
        def extract_text(self):
            return "decoded pdf text"

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    calls = []

    def fake_get_public_url(url, headers, timeout, max_redirects=5, max_bytes=None):
        calls.append(max_bytes)
        if len(calls) == 1:
            return content_mod._CappedFetch(
                200,
                {"Content-Type": "application/pdf", "content-length": "965256"},
                b"%PDF partial",
                True,
                965256,
                "utf-8",
                url,
            )
        return content_mod._CappedFetch(
            200,
            {"Content-Type": "application/pdf", "content-length": "965256"},
            b"%PDF full",
            False,
            965256,
            "utf-8",
            url,
        )

    monkeypatch.setattr(content_mod, "_get_public_url", fake_get_public_url)
    monkeypatch.setattr(content_mod, "pdf_extract_text", None)
    monkeypatch.setattr(content_mod, "PdfReader", _FakeReader)

    r = content_mod.fetch_webpage_content("https://example.com/compressed.pdf")

    assert r["success"] is True
    assert r["content"] == "[Page 1]\ndecoded pdf text"
    assert calls == [WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES]


def test_fetch_does_not_force_identity_encoding(monkeypatch, no_cache):
    # iter_bytes() is capped after decoding, so valid compressed pages should
    # not be rejected merely because a server ignores identity.
    seen = {}

    @contextmanager
    def fake_stream(method, url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        yield _FakeStream(b"hello")
    monkeypatch.setattr(content_mod.httpx, "stream", fake_stream)

    content_mod.fetch_webpage_content("https://example.com/a.txt")
    assert "Accept-Encoding" not in seen["headers"]


def test_accepts_compressed_response_and_caps_decoded_body(monkeypatch, no_cache):
    # Content-Length describes compressed wire bytes; the decoded stream still
    # obeys the normal soft cap.
    fake = _FakeStream(b"x" * 5000, content_length=40)
    fake.headers["content-encoding"] = "gzip"
    _patch_stream(monkeypatch, fake)
    r = content_mod.fetch_webpage_content("https://example.com/a.txt")
    assert r["success"] is True
    assert r["content"] == "x" * 5000
    assert fake.body_reads == 1


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
