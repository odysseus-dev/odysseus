"""Regression test for #3993 — live chat leaves executed tool fences visible.

The backend strips every fenced tool block (``src/tool_parsing.py`` builds its
regex from the full ``TOOL_TAGS`` set), so a reloaded session renders cleanly.
The live frontend path uses its own regex, ``EXEC_FENCE_RE`` in
``static/js/chatRenderer.js``.

Originally that regex came from a hand-maintained subset, so any executable tool
not in it — and every *future* tool added to ``TOOL_TAGS`` — left its executed
fence lingering as a raw code block in the live bubble until reload. The fix
makes ``TOOL_TAGS`` the single source: ``chatRenderer.js`` no longer hard-codes a
tool list at all. It fetches the backend's authoritative set once from
``GET /api/tools`` (which serves ``sorted(TOOL_TAGS)``) and builds
``EXEC_FENCE_RE`` from it at load, minus ``bash``/``python`` (legitimate code
examples a user may have asked the model to show). There is no second list to
drift.

``chatRenderer.js`` pulls browser globals and can't be imported under node, so
the behavioral tests exercise an equivalent Python regex built straight from the
backend ``TOOL_TAGS`` — the same source the live regex now derives from — and
source-level guards assert the frontend keeps no hard-coded list.
"""
import json
import re
from pathlib import Path

_SRC = Path("static/js/chatRenderer.js")
_ROUTES_SRC = Path("routes/model_routes.py")

# Deliberately NOT stripped: legitimate code-example languages, not tool
# invocations. Must match the carve-out in chatRenderer.js.
_NON_STRIPPED = {"bash", "python"}


def _tool_tags() -> set[str]:
    """The backend TOOL_TAGS set — the same authoritative set GET /api/tools
    serves (sorted) and the live EXEC_FENCE_RE derives from. Imported rather
    than source-scraped so it reflects the real set however it is composed: the
    literal plus the ``| BUILTIN_EMAIL_TOOLS`` union (email tool names live in
    that single source, not inline in the literal)."""
    from src.agent_tools import TOOL_TAGS
    return set(TOOL_TAGS)


def _exec_fence_regex() -> re.Pattern:
    """Rebuild EXEC_FENCE_RE's behavior from the same source the live regex now
    derives from: the backend TOOL_TAGS (served via /api/tools) minus bash/python."""
    tags = _tool_tags() - _NON_STRIPPED
    assert tags, "TOOL_TAGS is empty"
    return re.compile(
        r"```(" + "|".join(re.escape(tag) for tag in sorted(tags)) + r")(?![\w-])"
        r"[ \t]*([{\[][^\n]*?)?[ \t]*(?=\r?\n|```)\r?\n?([\s\S]*?)```",
        re.IGNORECASE,
    )


def _strip_live_exec_fences(text: str) -> str:
    rx = _exec_fence_regex()

    def repl(match: re.Match) -> str:
        inline = (match.group(2) or "").strip()
        if not inline:
            return ""
        body = (match.group(3) or "").strip()
        content = f"{inline}\n{body}" if body else inline
        try:
            json.loads(content)
        except (TypeError, ValueError):
            return match.group(0)
        return ""

    return rx.sub(repl, text)


def test_strips_executed_email_tool_fences():
    # The exact shape the reporter observed lingering in the live bubble.
    text = 'Here are emails\n\n```list_emails\n{"max_results":10}\n```'
    assert _strip_live_exec_fences(text).strip() == "Here are emails"


def test_strips_executed_inline_email_tool_fences():
    text = 'Here are accounts\n\n```list_email_accounts {}\n```'
    assert _strip_live_exec_fences(text).strip() == "Here are accounts"


def test_strips_multiline_inline_json_email_fences():
    text = 'Here are emails\n\n```list_emails {"folder": "INBOX",\n"max_results": 2}\n```'
    assert _strip_live_exec_fences(text).strip() == "Here are emails"


def test_strips_every_named_email_tool_fence():
    email_tools = [
        "list_email_accounts", "send_email", "list_emails", "read_email",
        "reply_to_email", "bulk_email", "archive_email", "delete_email",
        "mark_email_read",
    ]
    for tool in email_tools:
        fence = f"```{tool}\n{{}}\n```"
        assert _strip_live_exec_fences(fence).strip() == "", f"{tool} fence not stripped"


def test_preserves_existing_web_search_stripping():
    fence = '```web_search\n{"q":"x"}\n```'
    assert _strip_live_exec_fences(fence).strip() == ""


def test_does_not_strip_bash_or_python_code_examples():
    """bash/python fences are deliberately excluded — they are legitimate code
    examples a user may have asked the model to show, not tool invocations."""
    for lang in sorted(_NON_STRIPPED):
        example = f"```{lang}\nls -la\n```"
        assert _strip_live_exec_fences(example) == example, f"{lang} example wrongly stripped"


def test_does_not_strip_invalid_inline_json_metadata():
    for example in (
        '```list_email_accounts {title="setup"}\n```',
        '```web_search {query="odysseus"}\n```',
    ):
        assert _strip_live_exec_fences(example) == example


def test_frontend_keeps_no_hardcoded_tool_list():
    """Root-cause guard for #3993: chatRenderer.js must NOT reintroduce a
    hand-maintained tool list. A hard-coded mirror of TOOL_TAGS silently drifts
    when a new tool is added — leaving its executed fence in the live bubble
    until reload. The live regex must instead be built from the backend's
    authoritative set fetched at runtime."""
    source = _SRC.read_text(encoding="utf-8")
    assert "EXEC_TOOL_TAGS" not in source, (
        "chatRenderer.js reintroduced a hard-coded EXEC_TOOL_TAGS list; the "
        "live-strip tags must come from GET /api/tools so TOOL_TAGS stays the "
        "single source (#3993)."
    )
    assert "/api/tools" in source, (
        "chatRenderer.js must fetch the tool set from /api/tools to build "
        "EXEC_FENCE_RE."
    )
    assert "JSON.parse(content)" in source, (
        "chatRenderer.js must validate inline JSON before stripping same-line "
        "tool fences so Markdown metadata stays visible."
    )
    # The bash/python carve-out must survive the move to the runtime list.
    m = re.search(r"EXEC_FENCE_NON_TOOL\s*=\s*new Set\(\[(?P<body>.*?)\]\)", source, re.DOTALL)
    assert m, "bash/python carve-out (EXEC_FENCE_NON_TOOL) not found in chatRenderer.js"
    carve_out = set(re.findall(r"['\"]([a-z_]+)['\"]", m.group("body")))
    assert carve_out == _NON_STRIPPED, (
        f"EXEC_FENCE_NON_TOOL must carve out exactly {sorted(_NON_STRIPPED)}, "
        f"got {sorted(carve_out)}"
    )


def test_api_tools_endpoint_serves_full_tool_tags():
    """The frontend's single source is GET /api/tools. Guard that the endpoint
    serves the complete TOOL_TAGS set (sorted) — if it ever served a subset, the
    live-strip list would silently shrink with no second list to catch it."""
    source = _ROUTES_SRC.read_text(encoding="utf-8")
    assert re.search(r"for\s+tag\s+in\s+sorted\(\s*TOOL_TAGS\s*\)", source), (
        "GET /api/tools must iterate sorted(TOOL_TAGS) so the frontend's "
        "EXEC_FENCE_RE covers every executable tool (#3993)."
    )
