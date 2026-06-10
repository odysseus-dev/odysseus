"""_extract_report_title must not delete a mid-document section heading.

It picks the first non-generic heading as the report title and strips it from
the body so it doesn't duplicate the hero h1. But it stripped the chosen heading
unconditionally, so when the early headings were generic placeholders and the
first real heading appeared deeper in the body, that section's heading was
removed, orphaning its body and dropping it from the TOC. The heading is now
stripped only when it is the document's top heading.
"""
from src.visual_report import _extract_report_title


def test_top_heading_is_stripped():
    title, body = _extract_report_title("# Real Title\n\nbody text", "fallback")
    assert title == "Real Title"
    assert "# Real Title" not in body
    assert "body text" in body


def test_mid_document_heading_is_preserved():
    md = "## Introduction\nintro text\n\n## The Real Findings\nfindings here"
    title, body = _extract_report_title(md, "fallback")
    assert title == "The Real Findings"
    # The chosen section heading and its body must survive in the document.
    assert "## The Real Findings" in body
    assert "findings here" in body
    assert "## Introduction" in body


def test_fallback_when_no_heading():
    title, body = _extract_report_title("just prose, no heading", "the query")
    assert title == "the query"
    assert body == "just prose, no heading"
