"""Regression test for #3993 — live chat leaves executed tool fences visible.

The backend strips every fenced tool block (``src/tool_parsing.py`` builds its
regex from the full ``TOOL_TAGS`` set), so a reloaded session renders cleanly.
The live frontend path uses a *separate* regex, ``EXEC_FENCE_RE`` in
``static/js/chatRenderer.js``, built from an explicit ``EXEC_TOOL_TAGS`` list.

Originally that list was a hand-maintained subset (only web_search / read_file /
write_file / document / email tools), so any executable tool not in it — and
every *future* tool added to ``TOOL_TAGS`` — left its executed fence lingering as
a raw code block in the live bubble until reload. The fix makes
``EXEC_TOOL_TAGS`` mirror ``TOOL_TAGS`` minus ``bash``/``python`` (legitimate
code examples a user may have asked the model to show);
``test_exec_fence_re_covers_all_executable_tools`` fails on any drift.

``chatRenderer.js`` pulls browser globals and can't be imported under node, so we
extract ``EXEC_TOOL_TAGS`` from source and exercise an equivalent Python regex.
"""
import re
from pathlib import Path

_SRC = Path("static/js/chatRenderer.js")
_TOOLS_SRC = Path("src/agent_tools/__init__.py")

# Deliberately NOT stripped: legitimate code-example languages, not tool
# invocations. Must match the carve-out in chatRenderer.js.
_NON_STRIPPED = {"bash", "python"}


def _exec_tool_tags() -> list[str]:
    """Extract the EXEC_TOOL_TAGS array literal from chatRenderer.js."""
    source = _SRC.read_text(encoding="utf-8")
    m = re.search(r"const EXEC_TOOL_TAGS = \[(?P<body>.*?)\];", source, re.DOTALL)
    assert m, "EXEC_TOOL_TAGS array not found in chatRenderer.js"
    return re.findall(r"['\"]([a-z_]+)['\"]", m.group("body"))


def _exec_fence_regex() -> re.Pattern:
    """Rebuild EXEC_FENCE_RE's behavior from EXEC_TOOL_TAGS (mirrors chatRenderer.js)."""
    tags = _exec_tool_tags()
    assert tags, "EXEC_TOOL_TAGS is empty"
    return re.compile(r"```(?:" + "|".join(tags) + r")\s*\n[\s\S]*?```", re.IGNORECASE)


def _tool_tags() -> set[str]:
    """Extract the backend TOOL_TAGS set from src/agent_tools/__init__.py (source-level)."""
    source = _TOOLS_SRC.read_text(encoding="utf-8")
    m = re.search(r"TOOL_TAGS\s*=\s*\{(?P<body>.*?)\}", source, re.DOTALL)
    assert m, "TOOL_TAGS literal not found in src/agent_tools/__init__.py"
    return set(re.findall(r'"([a-z_]+)"', m.group("body")))


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
    for lang in sorted(_NON_STRIPPED):
        example = f"```{lang}\nls -la\n```"
        assert rx.sub("", example) == example, f"{lang} example wrongly stripped"


def test_exec_fence_re_covers_all_executable_tools():
    """Root-cause guard for #3993: the frontend EXEC_TOOL_TAGS must mirror the
    backend TOOL_TAGS (minus bash/python). A new tool added to TOOL_TAGS without
    updating chatRenderer.js would silently leave its executed fence in the live
    bubble until reload — this catches that drift in CI instead of by a user."""
    exec_tags = set(_exec_tool_tags())
    expected = _tool_tags() - _NON_STRIPPED
    missing = expected - exec_tags
    extra = exec_tags - expected
    assert not missing, (
        f"EXEC_TOOL_TAGS missing executable tools (their fences will linger in "
        f"the live stream): {sorted(missing)}"
    )
    assert not extra, (
        f"EXEC_TOOL_TAGS lists tools absent from TOOL_TAGS: {sorted(extra)}"
    )
    assert not (_NON_STRIPPED & exec_tags), (
        f"bash/python must stay excluded: {sorted(_NON_STRIPPED & exec_tags)}"
    )
