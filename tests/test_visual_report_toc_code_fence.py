"""TOC heading extraction must ignore headings inside code fences.

A "## ..." comment inside a ``` or ~~~ block is not rendered as an <h2>, but
_extract_headings counted it, so _apply_heading_ids (which zips TOC headings
against rendered <h2>/<h3> by position) gave later sections the wrong anchor
id and the trailing TOC link went dead.
"""
import pytest

pytest.importorskip("bs4")

from src.visual_report import _extract_headings


def test_backtick_fenced_heading_is_ignored():
    md = "## Intro\n\n```bash\n## not a heading\n```\n\n## Conclusion"
    assert [h["text"] for h in _extract_headings(md)] == ["Intro", "Conclusion"]


def test_tilde_fenced_heading_is_ignored():
    md = "## A\n\n~~~\n## fake\n~~~\n\n## B"
    assert [h["text"] for h in _extract_headings(md)] == ["A", "B"]


def test_normal_headings_unaffected():
    md = "## One\n\nsome text\n\n### Two"
    out = [(h["level"], h["text"]) for h in _extract_headings(md)]
    assert out == [(2, "One"), (3, "Two")]
