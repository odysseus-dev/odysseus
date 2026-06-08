"""Regression guards for API-provided research source hrefs."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def test_document_library_research_preview_whitelists_source_hrefs():
    src = (_REPO / "static" / "js" / "documentLibrary.js").read_text(encoding="utf-8")

    assert "function _safeResearchHref(raw)" in src
    assert "parsed.protocol === 'http:' || parsed.protocol === 'https:'" in src
    assert "const url = _safeResearchHref(src.url);" in src
    assert 'href="${_esc(url)}"' not in src
    assert "Failed to load: ${_esc(e.message)}" in src
    assert "Failed to load: ${e.message}" not in src


def test_research_panel_whitelists_source_hrefs():
    src = (_REPO / "static" / "js" / "research" / "panel.js").read_text(encoding="utf-8")

    assert "function _safeSourceHref(raw)" in src
    assert "parsed.protocol === 'http:' || parsed.protocol === 'https:'" in src
    assert "const url = _safeSourceHref(s.url);" in src
    assert 'const url = _esc(s.url || \'\');' not in src
