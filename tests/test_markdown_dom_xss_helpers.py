"""Regression guards for markdown raw-HTML sanitizer helpers."""

from pathlib import Path
from tests.helpers.document_source import document_source


_REPO = Path(__file__).resolve().parent.parent


def test_svg_title_extraction_never_reparses_model_output_as_dom():
    src = (_REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")
    render = src.split("function renderSvgSandbox(source)", 1)[1].split(
        "function replaceRawSvgBlocks", 1
    )[0]
    assert "innerHTML" not in render
    assert "DOMParser" not in render
    assert "parseFromString" not in render
    assert "decodeSvgTitleEntities" in render
    assert "([^<>]*)" in render
    assert "Visual explanation" in render


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
    document_src = document_source()
    email_body_helper = document_src.split("function _emailBodyToHtml(text)", 1)[1].split(
        "  // Mirror the rich body's plain text", 1
    )[0]

    assert "export function sanitizeAllowedHtml(html)" in markdown_src
    assert "sanitizeAllowedHtml," in markdown_src
    assert "markdownModule.sanitizeAllowedHtml(t)" in email_body_helper
    assert "return t;" not in email_body_helper
