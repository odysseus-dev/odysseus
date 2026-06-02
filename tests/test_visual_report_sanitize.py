"""Stored-XSS regression for deep-research report rendering.

The report body is the LLM's synthesis of SCRAPED third-party pages (untrusted)
and is interpolated unescaped into a page served with an inline-script CSP.
Python-Markdown's "extra" extension passes raw inline HTML through verbatim, so
without sanitization an attacker-controlled source page could land
<script>/onerror into a victim's report (executes same-origin with their
session cookie). `_md_to_html` must allowlist-sanitize its output.
"""

from src.visual_report import _md_to_html, _sanitize_html, _safe_url


def test_script_tag_is_removed_with_contents():
    out = _md_to_html("<script>alert('xss')</script>")
    assert "<script" not in out.lower()
    assert "alert('xss')" not in out  # decomposed, not just unwrapped


def test_inline_event_handler_attribute_stripped():
    out = _md_to_html('<img src="x" onerror="alert(1)">')
    assert "onerror" not in out.lower()
    assert "<img" in out.lower()  # the image itself is kept


def test_javascript_url_scheme_stripped():
    out = _md_to_html('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in out.lower()
    assert "click" in out  # link text preserved


def test_svg_script_payload_removed():
    out = _md_to_html("<svg><script>alert(1)</script></svg>")
    assert "alert(1)" not in out
    assert "<svg" not in out.lower()


def test_onclick_on_generic_element_stripped_but_text_kept():
    out = _md_to_html('<div onclick="evil()">hello</div>')
    assert "onclick" not in out.lower()
    assert "evil()" not in out
    assert "hello" in out


def test_inline_style_attribute_stripped():
    out = _md_to_html('<p style="x:expression(alert(1))">hi</p>')
    assert "style=" not in out.lower()
    assert "hi" in out


# --- must NOT break legitimate formatting ----------------------------------

def test_safe_markdown_formatting_preserved():
    out = _md_to_html("**bold** and `code` and [link](https://example.com)")
    assert "<strong>bold</strong>" in out
    assert "<code>code</code>" in out
    assert 'href="https://example.com"' in out
    assert 'target="_blank"' in out  # external-link rewrite still applied


def test_relative_and_fragment_links_kept():
    assert _safe_url("#section") is True
    assert _safe_url("/local/path") is True
    assert _safe_url("https://example.com") is True
    assert _safe_url("mailto:a@b.com") is True
    assert _safe_url("javascript:alert(1)") is False
    assert _safe_url("data:text/html,<script>") is False
    assert _safe_url("data:image/png;base64,AAAA", allow_data_image=True) is True
    assert _safe_url("data:image/png;base64,AAAA", allow_data_image=False) is False


def test_data_image_kept_on_img_but_html_data_uri_dropped():
    keep = _sanitize_html('<img src="data:image/png;base64,AAAA">')
    assert "data:image/png" in keep
    drop = _sanitize_html('<a href="data:text/html,<b>x">y</a>')
    assert "data:text/html" not in drop
