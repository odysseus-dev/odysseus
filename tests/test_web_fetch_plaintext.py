"""fetch_webpage_content must return plain-text and Markdown bodies verbatim.

raw.githubusercontent.com serves Markdown as `text/plain`, and a lot of code
and tool documentation lives in `.md` / `.txt`. Those have no HTML structure,
so the HTML branch extracted nothing and web_fetch reported "no readable text
content". The plain-text branch returns the body as-is. HTML stays on the
parsing path.
"""
import types

import pytest

from services.search import content as content_mod


class _FakeResponse:
    def __init__(self, text, content_type, status_code=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code

    def raise_for_status(self):
        return None


@pytest.fixture
def no_cache(monkeypatch, tmp_path):
    # Force a cache miss and skip disk writes so the test is hermetic.
    monkeypatch.setattr(content_mod, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content_mod, "_cache_result", lambda *a, **k: None)


def _patch_fetch(monkeypatch, text, content_type):
    monkeypatch.setattr(
        content_mod,
        "_get_public_url",
        lambda url, headers=None, timeout=5: _FakeResponse(text, content_type),
    )


MARKDOWN = "# Title\n\nSome **docs** with a [link](https://example.com).\n"


def test_markdown_text_plain_returns_body(monkeypatch, no_cache):
    _patch_fetch(monkeypatch, MARKDOWN, "text/plain; charset=utf-8")
    r = content_mod.fetch_webpage_content(
        "https://raw.githubusercontent.com/o/r/master/Documentation/Patterns.md"
    )
    assert r["success"] is True
    assert r["content"] == MARKDOWN.strip()
    assert r["title"] == "patterns.md"
    assert r["error"] == ""


def test_text_markdown_content_type_returns_body(monkeypatch, no_cache):
    _patch_fetch(monkeypatch, MARKDOWN, "text/markdown")
    r = content_mod.fetch_webpage_content("https://example.com/readme")
    assert r["success"] is True
    assert r["content"] == MARKDOWN.strip()


def test_octet_stream_with_txt_suffix_returns_body(monkeypatch, no_cache):
    # Some servers mislabel text files; the URL-suffix fallback still reads it.
    _patch_fetch(monkeypatch, "plain notes\nline two\n", "application/octet-stream")
    r = content_mod.fetch_webpage_content("https://example.com/notes.txt")
    assert r["success"] is True
    assert r["content"] == "plain notes\nline two"


def test_application_json_returns_body(monkeypatch, no_cache):
    # application/json is not text/*; it must still be returned verbatim
    # instead of being fed to the HTML parser (which yields empty content).
    body = '{"name": "odysseus", "items": [1, 2, 3]}'
    _patch_fetch(monkeypatch, body, "application/json")
    r = content_mod.fetch_webpage_content("https://api.example.com/data")
    assert r["success"] is True
    assert r["content"] == body


def test_ld_json_suffix_content_type_returns_body(monkeypatch, no_cache):
    body = '{"@context": "https://schema.org"}'
    _patch_fetch(monkeypatch, body, "application/ld+json")
    r = content_mod.fetch_webpage_content("https://example.com/meta")
    assert r["success"] is True
    assert r["content"] == body


def test_json_suffix_with_octet_stream_returns_body(monkeypatch, no_cache):
    body = '{"raw": true}'
    _patch_fetch(monkeypatch, body, "application/octet-stream")
    r = content_mod.fetch_webpage_content("https://example.com/package.json")
    assert r["success"] is True
    assert r["content"] == body


def test_empty_text_body_is_not_success(monkeypatch, no_cache):
    _patch_fetch(monkeypatch, "   \n  ", "text/plain")
    r = content_mod.fetch_webpage_content("https://example.com/blank.txt")
    assert r["success"] is False
    assert r["content"] == ""


def test_html_still_uses_parser(monkeypatch, no_cache):
    # An HTML body must not be short-circuited by the text branch.
    html = "<html><head><title>Hi</title></head><body><p>Hello world body text</p></body></html>"
    _patch_fetch(monkeypatch, html, "text/html; charset=utf-8")
    r = content_mod.fetch_webpage_content("https://example.com/page")
    assert r["title"] == "Hi"
    assert "Hello world body text" in r["content"]
