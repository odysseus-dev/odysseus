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
