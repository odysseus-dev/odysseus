"""Regression tests for the residual py/polynomial-redos fixes (PR #6503 lane).

Each rewritten matcher is pinned two ways:
  * equivalence with the regex it replaced on ordinary inputs, and
  * the CodeQL-reported adversarial shape completing within a loose budget
    (the replaced regexes took seconds to tens of seconds on these sizes).
"""

import re
import time
from pathlib import Path

import pytest

from src.text_helpers import strip_closed_think_blocks

_BUDGET_S = 2.0
_ROOT = Path(__file__).resolve().parents[1]


def _fast(fn, *args):
    started = time.perf_counter()
    result = fn(*args)
    elapsed = time.perf_counter() - started
    assert elapsed < _BUDGET_S, f"{getattr(fn, '__name__', fn)} took {elapsed:.2f}s"
    return result


# -- closed <think> blocks (skills_routes x4, memory_extractor.audit_memories) --

_THINK_REF = re.compile(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", re.I)


@pytest.mark.parametrize("text", [
    "",
    "plain",
    "<think>x</think>{\"ok\": true}",
    "a<thinking>b</thinking>c<think>d</think>e",
    "<think>a<think>nested</think>rest</think>",
    "orphan</think> then <think>unclosed",
    "<THINK>case</Thinking>kept",
    "<think>\nmulti\nline\n</think>\n{}",
])
def test_strip_closed_think_blocks_matches_reference(text):
    assert strip_closed_think_blocks(text) == _THINK_REF.sub("", text)


def test_strip_closed_think_blocks_unclosed_opener_flood_is_linear():
    flood = "<think>" * 40_000
    assert _fast(strip_closed_think_blocks, flood) == flood


def test_lazy_think_regex_is_gone_from_llm_review_parsers():
    for rel in ("routes/skills_routes.py", "services/memory/memory_extractor.py"):
        source = (_ROOT / rel).read_text(encoding="utf-8")
        assert r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>" not in source, rel


# -- email compose markdown links (email_routes._md_to_email_html) -----------

_LINK_REF = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


@pytest.mark.parametrize("text", [
    "see [docs](https://example.com/a) and [b](http://x.y)",
    "[a[b](http://x)",
    "[]()[x](http://y)",
    "[a](http://x [b c](http://y)",
    "[a](http://)[b](https://z)",
    "[a](ftp://x) [b](https://ok)",
    "[unterminated(http://x)",
    "[t](http://u) tail ] [",
])
def test_md_links_to_html_matches_reference(text):
    from routes.email.email_routes import _md_links_to_html

    assert _md_links_to_html(text) == _LINK_REF.sub(r'<a href="\2">\1</a>', text)


@pytest.mark.parametrize("flood", [
    "[" + "[\\" * 40_000,          # CodeQL's reported shape
    "[a](http://x" * 8_000,        # URL that never closes, repeated
])
def test_md_links_to_html_floods_are_linear(flood):
    from routes.email.email_routes import _md_links_to_html

    assert _fast(_md_links_to_html, flood) == flood


def test_md_to_email_html_still_renders_and_escapes_links():
    from routes.email.email_routes import _md_to_email_html

    html = _md_to_email_html("**hi** [site](https://example.com/x)\n<script>")
    assert '<a href="https://example.com/x">site</a>' in html
    assert "<strong>hi</strong>" in html
    assert "<script>" not in html


# -- PDF URL detection (chat_routes._prefers_structured_document_tools) ------

_PDF_REF = re.compile(r"https?://[^\s]+(?:\.pdf\b|/pdf/)", re.I)


@pytest.mark.parametrize("text", [
    "read https://arxiv.org/pdf/2401.0001 please",
    "https://x.org/paper.PDF",
    "https://x.org/paper.pdfx",
    "xhttps://a/b.pdf",
    "http:///pdf/",
    "http://a .pdf",
    "no links here.pdf",
    "https://a/b http://c.pdf",
])
def test_mentions_pdf_url_matches_reference(text):
    from routes.chat_routes import _mentions_pdf_url

    assert _mentions_pdf_url(text) is bool(_PDF_REF.search(text))


def test_mentions_pdf_url_scheme_flood_is_linear():
    from routes.chat_routes import _prefers_structured_document_tools

    assert _fast(_prefers_structured_document_tools, "http://" * 8_000) is False


# -- chat_helpers persistence cleanup ----------------------------------------

def test_clean_repeated_assistant_content_trailing_think_closer():
    from routes.chat_helpers import clean_repeated_assistant_content

    prose = "word " * 30 + "done"  # past the 120-char edge-closer window
    assert clean_repeated_assistant_content(prose + "  </think>  ") == prose
    whitespace_run = "a" + "\t" * 80_000 + "x"
    assert _fast(clean_repeated_assistant_content, whitespace_run) == whitespace_run


@pytest.mark.parametrize("text,expected", [
    ("The user wants a hi.\n\n<think>Hey there!</think>",
     "<think>The user wants a hi.</think>\nHey there!"),
    ("I should greet.\n<thinking>  Hello  ", "<think>I should greet.</think>\nHello"),
    ("Plain answer <think>x</think>", "Plain answer <think>x</think>"),
])
def test_normalize_thinking_garbled_tags_unchanged(text, expected):
    from routes.chat_helpers import _normalize_thinking

    assert _normalize_thinking(text) == expected


@pytest.mark.parametrize("flood", [
    "a" + "\n" * 80_000 + "x",
    "The user x\n<think>" + "\t" * 80_000 + "x",
])
def test_normalize_thinking_floods_are_linear(flood):
    from routes.chat_helpers import _normalize_thinking

    _fast(_normalize_thinking, flood)


# -- calendar natural-language datetimes (GET /events, POST /events) ---------

def test_calendar_time_first_phrases_still_parse():
    from routes.calendar_routes import _parse_dt, parse_due_for_user

    parsed = _parse_dt("3pm tomorrow")
    assert (parsed.hour, parsed.minute) == (15, 0)
    assert "T09:00:00" in parse_due_for_user("9 a.m today")
    assert "T23:00:00" in parse_due_for_user("11pm tonight")


@pytest.mark.parametrize("payload", [
    "a" + "\t" * 80_000 + "x",       # CodeQL's reported shape (time-first regex)
    "a" + "\t" * 80_000 + "m",       # am/pm normaliser on the same input
])
def test_calendar_parsers_whitespace_flood_is_linear(payload):
    from routes.calendar_routes import _parse_dt, parse_due_for_user

    for fn in (_parse_dt, parse_due_for_user):
        started = time.perf_counter()
        try:
            fn(payload)
        except Exception:
            pass  # unparseable is fine; only the time matters here
        assert time.perf_counter() - started < _BUDGET_S, fn.__name__
