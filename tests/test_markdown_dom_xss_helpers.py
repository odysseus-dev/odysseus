"""Regression guards for markdown raw-HTML sanitizer helpers."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def test_markdown_raw_html_sanitizer_checks_url_attr_edge_cases():
    src = (_REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")

    assert "function _compactUrlSchemeValue(value)" in src
    assert "function _isDangerousUrl(value)" in src
    assert "function _isDangerousSrcset(value)" in src
    assert "'srcset'" in src
    assert "candidate => _isDangerousUrl(candidate)" in src
    assert "name === 'srcset' ? _isDangerousSrcset(attr.value) : _isDangerousUrl(attr.value)" in src


def test_markdown_raw_html_sanitizer_strips_scriptable_css():
    src = (_REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")

    assert "if (name === 'style')" in src
    assert r"javascript:|vbscript:|data:|expression\(" in src
    assert "el.removeAttribute(attr.name);" in src


def test_email_rich_body_render_path_reuses_raw_html_sanitizer():
    markdown_src = (_REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")
    document_src = (_REPO / "static" / "js" / "document.js").read_text(encoding="utf-8")
    email_body_helper = document_src.split("function _emailBodyToHtml(text)", 1)[1].split(
        "  // Mirror the rich body's plain text", 1
    )[0]

    assert "export function sanitizeAllowedHtml(html)" in markdown_src
    assert "sanitizeAllowedHtml," in markdown_src
    assert "markdownModule.sanitizeAllowedHtml(t)" in email_body_helper
    assert "return t;" not in email_body_helper


def test_markdown_raw_html_sanitizer_img_data_uri_exception_is_strict():
    """The <img src> exception for inline generated images (issue #5436) must
    stay anchored to the base64 raster form and must not weaken the general
    data:/javascript: strip for every other URL-bearing attribute."""
    src = (_REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")

    # Strict pattern: raster formats only (no SVG), base64 payload chars only.
    assert r"/^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i" in src
    # Exception is scoped to src on IMG elements and the anchored pattern.
    assert "name === 'src' && el.tagName.toUpperCase() === 'IMG'" in src
    assert "SAFE_DATA_IMAGE_RE.test(attr.value)" in src
    # The general scrub for all other URL-bearing attributes is unchanged.
    assert "name === 'srcset' ? _isDangerousSrcset(attr.value) : _isDangerousUrl(attr.value)" in src
