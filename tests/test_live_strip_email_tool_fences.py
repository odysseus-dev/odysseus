"""Regression test for #3993 — live chat leaves executed email tool fences visible.

The backend strips every fenced tool block (``src/tool_parsing.py`` builds its
regex from the full ``TOOL_TAGS`` set), so a reloaded session renders cleanly.
The live frontend path uses a *separate* hardcoded regex,
``EXEC_FENCE_RE`` in ``static/js/chatRenderer.js``. Before the fix that regex
only listed ``web_search|read_file|write_file|create_document|edit_document|
update_document`` — so executed *email* tool fences (``list_emails`` etc.) were
never stripped from the live stream and lingered as raw code blocks until reload.

``chatRenderer.js`` pulls browser globals and can't be imported under node, so we
extract the ``EXEC_FENCE_RE`` literal from source and exercise an equivalent
Python regex (the pattern is a plain alternation + ``[\\s\\S]`` body).
"""
import re
from pathlib import Path

_SRC = Path("static/js/chatRenderer.js")


def _exec_fence_regex() -> re.Pattern:
    """Extract EXEC_FENCE_RE from chatRenderer.js as an equivalent Python regex."""
    source = _SRC.read_text(encoding="utf-8")
    m = re.search(r"const EXEC_FENCE_RE = /(?P<body>.+?)/gi;", source)
    assert m, "EXEC_FENCE_RE literal not found in chatRenderer.js"
    # JS [\s\S] / (?:...) / \s / \n all carry over to Python verbatim.
    return re.compile(m.group("body"), re.IGNORECASE)


def test_strips_executed_email_tool_fences():
    rx = _exec_fence_regex()
    # The exact shape the reporter observed lingering in the live bubble.
    text = 'Here are emails\n\n```list_emails\n{"max_results":10}\n```'
    assert rx.sub("", text).strip() == "Here are emails"


def test_strips_every_named_email_tool_fence():
    rx = _exec_fence_regex()
    email_tools = [
        "list_email_accounts", "send_email", "list_emails", "read_email",
        "reply_to_email", "bulk_email", "archive_email", "delete_email",
        "mark_email_read",
    ]
    for tool in email_tools:
        fence = f"```{tool}\n{{}}\n```"
        assert rx.sub("", fence).strip() == "", f"{tool} fence not stripped"


def test_preserves_existing_web_search_stripping():
    rx = _exec_fence_regex()
    fence = '```web_search\n{"q":"x"}\n```'
    assert rx.sub("", fence).strip() == ""


def test_does_not_strip_bash_or_python_code_examples():
    """bash/python fences are deliberately excluded — they are legitimate code
    examples a user may have asked the model to show, not tool invocations."""
    rx = _exec_fence_regex()
    for lang in ("bash", "python"):
        example = f"```{lang}\nls -la\n```"
        assert rx.sub("", example) == example, f"{lang} example wrongly stripped"
