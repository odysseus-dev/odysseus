"""Preserve legacy text-call/listing semantics and bound hostile scans."""

import itertools
import random
import re
import subprocess
import sys
import textwrap

import pytest

from src.agent_loop import (
    _calendar_listing_row,
    _captures_after_first_prefix,
    _contains_email_draft_headers,
    _looks_like_agent_reasoning_preamble,
    _looks_like_ody_qwen_leaked_tool_text,
    _email_account_label,
    _email_sender_name,
    _contextual_summary_fragment,
    _is_terse_link_request,
    _is_terse_email_lookup_followup,
    _looks_like_destructive_request,
    _looks_like_youtube_tool_turn,
    _mentions_pdf_url,
    _mentions_workspace_script,
    _numbered_row_parenthesized_ids,
    _pipeline_request_parts,
    _parse_explicit_open_panel_request,
    _private_browser_product_query,
    _read_only_shell_command,
    _remaining_checklist_name,
    _research_listing_row,
    _session_link_from_row,
    _session_find_query_capture,
    _session_list_summary_from_tool_output,
    _split_before_assistant_prompt,
    _split_note_items,
    _strip_trailing_done,
    _strip_horizontal_space_before_lf,
    _skill_listing_row,
)
from src.clean_agent_preview import (
    _page_listing_request,
    _prior_web_source_request,
    _terminal_source_link_clause,
    _web_source_rows,
    declared_workspace_artifacts,
)
from src.text_scanning import (
    contains_detailed_sequence_request,
    contains_search_engine_navigation,
    iter_angle_contents,
    iter_markdown_links,
    first_tag_content,
    replace_markdown_links_with_labels,
)
from src.turn_contract import (
    _WARM_RECALL,
    _WARM_RECALL_WITH_FOLLOWUP,
    _is_pure_action_prohibition,
    _mentions_under_budget,
    _mentions_workspace_artifact,
    _mentions_workspace_output,
    _strip_cancelled_request_lead,
    _strip_terminal_but,
    _terminal_clause_match,
    _warm_recall_parts,
)
from src.tool_parsing import (
    _GEMMA_TOOL_CALL_OPEN_RE,
    _GEMMA_TOOL_CALL_CLOSE_RE,
    _QWEN_FUNCTION_OPEN_RE,
    _QWEN_FUNCTION_CLOSE_RE,
    _QWEN_PARAMETER_OPEN_RE,
    _QWEN_PARAMETER_CLOSE_RE,
    _iter_named_blocks,
    _iter_qwen_python_args,
    _strip_delimited,
    parse_tool_blocks,
    strip_tool_blocks,
    strip_angle_tags,
    iter_email_addresses,
)

_SESSION_LINK = re.compile(r"(\[(?:\\.|[^\]])+\]\(#session-[^)]+\))")
_GEMMA = re.compile(
    r"<\|?tool_call\|?>\s*call:([\w\d_-]+)\s*(\{[\s\S]*?\})\s*<\|?tool_call\|?>",
    re.I,
)
_QWEN_FUNCTION = re.compile(r"<function=([A-Za-z_][\w:.-]*)>\s*([\s\S]*?)\s*</function>")
_QWEN_PARAMETER = re.compile(r"<parameter=([A-Za-z_]\w*)>\s*([\s\S]*?)\s*</parameter>")
_QWEN_ARGS = re.compile(r"([A-Za-z_]\w*)\s*=\s*(['\"].*?['\"]|[^,]+)")
_WORKSPACE_SCRIPT = re.compile(
    r"/workspace/[^\s`\"']+\.(?:py|pyw|sh|bash|js|mjs|ts|rb|pl)\b", re.I
)
_PDF_URL = re.compile(r"https?://\S+(?:\.pdf\b|/pdf/)", re.I)
_WORKSPACE_ARTIFACT = re.compile(
    r"(?:file://)?/workspace/[^\s`\"']+\.(?:csv|html?|json|md|svg|txt)\b", re.I
)
_WORKSPACE_OUTPUT = re.compile(
    r"(?:file://)?/workspace/(?!input/)[^\s`\"']+\."
    r"(?:csv|html?|json|md|svg|txt|avif|bmp|gif|jpe?g|png|webp|pdf|mp4|webm)\b",
    re.I,
)
_SEARCH_ENGINE_URL = re.compile(
    r"https?://(?:[^/]+\.)?(?:google\.[^/]+|bing\.com|duckduckgo\.com)"
    r"/(?:search|sorry|html|lite|\?)",
    re.I,
)
_LEAKED_TOOL_TEXT = re.compile(
    r"(<\s*/?\s*(?:function|parameter|tool_call)\b|(?:^|\n)\s*(?:function|parameter)\s*="
    r"|\bmanage_(?:notes|calendar|memory|documents|contact)\s*\(|\"function\"\s*:\s*\"(?:manage_|mcp__)"
    r"|mcp__email__|(?:^|\n)\s*(?:web_search|web_fetch|private_browser)\s*:)", re.I,
)
_VIDEO_DETAIL_BASE = re.compile(
    r"\b(?:how\s+many|count|break\s*points?|timestamps?|what\s+time|"
    r"when\s+.*(?:end|happen)|score(?:board)?s?)\b", re.I,
)
_VIDEO_DETAIL_EXTENDED = re.compile(
    r"\b(?:how\s+many|count|sequence|in\s+order|chronological|timestamps?|"
    r"what\s+time|at\s+what\s+time|when\s+.*(?:end|happen)|"
    r"first\s+.*(?:save|attempt|event)|score(?:board)?s?)\b|"
    r"(?:多少|几次|何时|什么时候|时间|顺序)", re.I,
)


def test_session_link_matches_legacy_escape_and_greedy_semantics():
    cases = [
        "no links", "[](#session-id)", "[x](#session-)",
        "[x](#session-id)", "[[x](#session-id)",
        r"[x\](#session-id)", r"[x\]more](#session-id)",
        r"[x\](#session-first)\](#session-last)",
        r"[x\](#session-first)\](#session-)",
        r"[x\](#session-first)\](#session-unclosed",
        "[bad] then [good](#session-id)",
        "[x](#session-id[has]brackets)",
        "[x](#session-first) [y](#session-second)",
    ]
    rng = random.Random(6503)
    tokens = ["[", "]", "\\", "a", "(", ")", "(#session-id)", "(#session-)"]
    cases += ["".join(rng.choices(tokens, k=12)) for _ in range(2000)]
    cases += ["[" + "".join(label) + "](#session-id)"
              for n in range(5) for label in itertools.product("a[]\\", repeat=n)]
    for row in cases:
        expected = _SESSION_LINK.search(row)
        assert _session_link_from_row(row) == (expected.group(0) if expected else ""), row


@pytest.mark.parametrize("row,expected", [
    ("- **[Chat](#session-id)** (id: `id`, model: qwen, 2 msgs, last active today)",
     "- [Chat](#session-id) (last active today)"),
    ("- [Chat](#session-id) (irrelevant) (model: qwen) (last active later)",
     "- [Chat](#session-id)"),
    ("- [Chat](#session-id) (outer (last active today))",
     "- [Chat](#session-id) (last active today)"),
    ("- plain row", "- plain row"),
])
def test_session_summary_preserves_link_and_metadata(row, expected):
    assert _session_list_summary_from_tool_output("Chats:\n" + row) == "Chats:\n" + expected


def test_gemma_delimiters_match_legacy_parse_and_strip():
    cases = [
        "ordinary prose", "<|tool_call|>call:web_search{query: 'news'}<|tool_call|>",
        "before<tool_call> call:read-file {\npath: 'README.md'\n} <tool_call>after",
        "<tool_call>call:x{a}<tool_call>call:y{b}<tool_call>",
        "<tool_call>call:x{<tool_call>call:y{b}<tool_call>",
        "<tool_call>call:x{unclosed", "}<tool_call><tool_call>call:x{unclosed",
    ]
    for text in cases:
        actual = [(name, "{" + body + "}") for name, body in _iter_named_blocks(
            text, _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE
        )]
        assert actual == _GEMMA.findall(text)
        assert _strip_delimited(text, _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE) == _GEMMA.sub("", text)
    raw = '<|tool_call|>call:web_search{"query":"news"}<|tool_call|>'
    assert [(b.tool_type, b.content) for b in parse_tool_blocks(raw)] == [("web_search", "news")]
    assert strip_tool_blocks(raw) == ""


@pytest.mark.parametrize("reference,opener,closer,tokens", [
    (_QWEN_FUNCTION, _QWEN_FUNCTION_OPEN_RE, _QWEN_FUNCTION_CLOSE_RE,
     ["<function=manage_notes>", "</function>", "a", "\n", "\t", " "]),
    (_QWEN_PARAMETER, _QWEN_PARAMETER_OPEN_RE, _QWEN_PARAMETER_CLOSE_RE,
     ["<parameter=query>", "</parameter>", "a", "\n", "\t", " "]),
])
def test_qwen_delimiters_preserve_names_and_stripped_values(reference, opener, closer, tokens):
    rng = random.Random(6503)
    for _ in range(1000):
        text = "".join(rng.choices(tokens, k=16))
        expected = [(name, body.strip()) for name, body in reference.findall(text)]
        actual = [(name, body.strip()) for name, body in _iter_named_blocks(text, opener, closer)]
        assert actual == expected


def test_qwen_python_arguments_preserve_permissive_legacy_grammar():
    cases = ["action='list', limit=5", "action = \"list\"", "1key=2", "aé=4",
             "key='mismatched\"", "broken word, okay=2", "a=\t, b=3", "a=\n'hi'", "a='hi\nthere'"]
    rng = random.Random(6503)
    tokens = ["key", "1", "中", "é", "=", "\n", " ", "\t", "'", '"', ",", "-", "_", "[]"]
    cases += ["".join(rng.choices(tokens, k=20)) for _ in range(2000)]
    for text in cases:
        assert list(_iter_qwen_python_args(text)) == _QWEN_ARGS.findall(text), text


@pytest.mark.parametrize("text", [
    "Done.", "Done.\nUpdated the document.\nDone.", "Done. undone.",
    "Done.\nDONE.\t", "Done.\u2003Done.\u2003", "Done. no terminal marker   ",
])
def test_trailing_done_matches_legacy_cleanup(text):
    pattern = re.compile(r"\s*Done\.\s*$", re.I)
    expected = pattern.sub("", text).rstrip() if pattern.search(text) else text
    assert _strip_trailing_done(text) == expected


@pytest.mark.parametrize("allow_empty", [False, True])
@pytest.mark.parametrize("replacement", ["", " "])
def test_angle_tag_cleanup_matches_legacy_flat_grammar(allow_empty, replacement):
    pattern = re.compile(r"<[^>]*>" if allow_empty else r"<[^>]+>")
    cases = ["before<b>bold</b>after", "<>", "<<>>", "a<x\ny>b", "<broken",
             "<a><b>one</b></a>", "a > b", "<x title='>'>tail"]
    cases += ["".join(parts) for n in range(6)
              for parts in itertools.product("a<>\n", repeat=n)]
    for text in cases:
        assert strip_angle_tags(text, replacement, allow_empty=allow_empty) == pattern.sub(replacement, text)


@pytest.mark.parametrize("ascii_only", [False, True])
def test_email_scanner_preserves_legacy_address_sets(ascii_only):
    pattern = re.compile(
        r"[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        if ascii_only else r"[\w.+-]+@[\w.-]+\.\w+"
    )
    cases = ["a@b.example", "a+b@b.example, c@d.example", "a@b@c.example",
             "a@b.c+d@e.f", "你好@例子.中国", "a@b.-.com", "'a'@example.com",
             "a@b.x", "a@.com", "a@b...", "a@b.c@d.example"]
    rng = random.Random(6503)
    tokens = ["a", "b", "é", "中", "@", ".", "-", "+", " ", "_", "'", "!", "/"]
    cases += ["".join(rng.choices(tokens, k=30)) for _ in range(3000)]
    for text in cases:
        assert list(iter_email_addresses(text, ascii_only=ascii_only)) == pattern.findall(text), text


def test_prefixed_workspace_and_url_detectors_match_legacy_searches():
    cases = [
        "", "/workspace/a.py", "FILE:///WORKSPACE/report.HTML",
        "/workspace/input/a.png", "/workspace/input/a.png/workspace/out.png",
        "http://example.test/a.pdf", "http://x/http://example.test/pdf/view",
        "https://google.com/search?q=x", "http://news.google.co.uk/sorry/index",
        "https://x.bing.com/html", "http://bad/http://duckduckgo.com/?q=x",
    ]
    rng = random.Random(6503)
    tokens = ["/workspace/", "input/", "file://", "http://", "https://", ".py",
              ".pdf", ".html", "/pdf/", "google.", "bing.com/", "search", "x", " ", "'", "/"]
    cases += ["".join(rng.choices(tokens, k=18)) for _ in range(4000)]
    for text in cases:
        assert _mentions_workspace_script(text) == bool(_WORKSPACE_SCRIPT.search(text)), text
        assert _mentions_pdf_url(text) == bool(_PDF_URL.search(text)), text
        assert _mentions_workspace_artifact(text) == bool(_WORKSPACE_ARTIFACT.search(text)), text
        assert _mentions_workspace_output(text) == bool(_WORKSPACE_OUTPUT.search(text)), text
        assert contains_search_engine_navigation(text) == bool(_SEARCH_ENGINE_URL.search(text)), text


def test_declared_workspace_artifacts_keeps_legacy_greedy_paths():
    pattern = re.compile(r"/workspace/[^\s,，、;；`\"'<>]+\.[A-Za-z0-9]{1,12}", re.I)
    cases = [
        "create /workspace/report.csv",
        "read /workspace/input.csv and create /workspace/out.json",
        "create /workspace/a.csv/workspace/b.json",
        "create /workspace/noext /workspace/out.txt",
    ]
    for text in cases:
        expected = []
        for match in pattern.finditer(text):
            path = match.group(0).rstrip(".!?)）]}")
            if path.startswith("/workspace/fixtures/") or path in expected:
                continue
            before = text[max(0, match.start() - 240):match.start()]
            before = pattern.sub("[workspace file]", before)
            clause = re.split(r"[.;!?\n]", before)[-1]
            if re.search(
                r"\b(?:from|using|inspect|read|open|analy[sz]e|transcribe|extract\s+(?:text\s+)?from|"
                r"input(?:\s+file)?(?:\s+is)?|source(?:\s+file)?(?:\s+is)?)\s*(?::|=)?\s*$",
                clause, re.I,
            ) or re.search(r"\b(?:read_file|inspect_media|extract_text|transcribe_media|pdf_extract)\b", clause, re.I):
                continue
            if not re.search(
                r"\b(?:create|write|save|export|render|generate|produce|output|deliver|store|convert|make)\b|"
                r"\b(?:write_file|output_path)\b", clause, re.I,
            ):
                continue
            expected.append(path)
        assert declared_workspace_artifacts(text) == tuple(expected)


def test_intent_and_detail_detectors_preserve_short_legacy_language():
    cases = [
        "plain answer", "\n\nfunction = manage_notes", "mcp__email__read_email",
        "< function >", "web_search: cats", "prefix\n private_browser : url",
        "when does it end", "when\nwill it end", "first save event", "sequence please",
        "I can now carefully inspect the result.", "Answer. Now let me verify this.",
        "接下来我会查看结果", "。   让我先检查",
    ]
    rng = random.Random(6503)
    tokens = ["\n", " ", ".", "!", "function", "parameter", "=", "web_search", ":",
              "when", "first", "end", "event", "let me", "inspect", "x"]
    cases += ["".join(rng.choices(tokens, k=24)) for _ in range(3000)]
    for text in cases:
        assert _looks_like_ody_qwen_leaked_tool_text(text) == bool(_LEAKED_TOOL_TEXT.search(text)), text
        assert contains_detailed_sequence_request(text, include_first=False) == bool(_VIDEO_DETAIL_BASE.search(text)), text
        assert contains_detailed_sequence_request(text) == bool(_VIDEO_DETAIL_EXTENDED.search(text)), text

    for text in (
        "The result is partial. Now let me inspect the rest.",
        "\n" * 20 + "let me check the source",
        "。\n  接下来我会查看结果",
        "A complete factual answer.",
    ):
        assert isinstance(_looks_like_agent_reasoning_preamble(text), bool)


def test_suffix_and_split_helpers_preserve_legacy_results():
    panel_cases = [
        ("open calendar again!!!", ("ui_control", "open_panel calendar")),
        ("open calendar" + " " * 50 + "again?", ("ui_control", "open_panel calendar")),
        ("open calendar....", ("ui_control", "open_panel calendar")),
        ("open calendar again x", ("ui_control", "open_panel calendar")),
        ("open notes day view again.", ("ui_control", "open_panel notes")),
    ]
    for text, expected in panel_cases:
        assert _parse_explicit_open_panel_request(text) == expected

    values = ["one, two and three", " one ,two  and  three ", "candy and x", "a,and,b", ""]
    for value in values:
        expected_parts = [
            re.sub(r"\s+", " ", part).strip(" .")
            for part in re.split(r"\s*,\s*|\s+\band\b\s+", value)
        ]
        expected = [{"text": part, "done": False} for part in expected_parts if part]
        assert _split_note_items(value) == expected

    for value in ["Alice <a@example.com>", "Alice <<a@example.com>", "Alice <>",
                  "Alice (a@example.com)", "Alice ((a@example.com)", "Alice > x <a>"]:
        normalized = re.sub(r"\s+", " ", value).strip()
        expected_sender = re.sub(r"\s*\([^)]*@[^)]*\)\s*$", "", normalized).strip()
        expected_sender = re.sub(r"\s*<[^>]*>\s*$", "", expected_sender).strip()
        expected_sender = expected_sender or value.strip()
        expected_account = re.sub(r"\s*<[^>]+>\s*$", "", normalized).strip() or normalized
        assert _email_sender_name(value) == expected_sender
        assert _email_account_label(value) == expected_account

    for value in ["Reply body\nWant me to send it?", "Reply\n\n  SHOULD I continue?", "Want me now", "x\nnot a prompt"]:
        expected = re.split(r"\n\s*(?:Want me|Would you like|Should I)\b", value, flags=re.I, maxsplit=1)[0]
        assert _split_before_assistant_prompt(value) == expected

    for value in ["a   \n b\t\n", "   x", "a \r\n", "\t\n\n", ""]:
        assert _strip_horizontal_space_before_lf(value) == re.sub(r"[ \t]+\n", "\n", value)

    for value in ["pwd && ls", "cat x | grep y", "echo x; printf y", "pwd " + " " * 20 + "&& ls", "pwd || rm x"]:
        legacy_parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", value.strip())
        allowed = re.compile(
            r"^(?:pwd|ls|find|rg|grep|git\s+(?:status|diff|log|show|branch)|sed(?!\s+-i\b)|"
            r"head|tail|cat|stat|file|wc|sort|uniq|cut|ip|ipconfig|getent|nslookup|dig|arp|"
            r"hostname|uname|whoami|echo|printf|test|true|false|:)\b", re.I,
        )
        expected = bool(legacy_parts) and all(part.strip() for part in legacy_parts) and all(
            allowed.match(part.strip()) for part in legacy_parts
        )
        assert _read_only_shell_command(value) == bool(expected)


@pytest.mark.parametrize("prefix,target_pattern", [
    ("#session-", r"[^)]+"),
    ("#note-", r"[^)]+"),
    ("#event-", r"[0-9a-fA-F-]{8,64}"),
    ("", r"[^)]+"),
])
def test_forward_markdown_and_angle_scanners_match_legacy(prefix, target_pattern):
    reference = re.compile(r"\[([^\]]+)\]\(" + re.escape(prefix) + "(" + target_pattern + r")\)")
    validator = None if target_pattern == r"[^)]+" else re.compile(target_pattern)
    cases = ["[title](#session-id)", "[[title](#session-id)", "[](x)",
             "[a](x)[b](y)", "[a](#event-12345678)", "[a](broken"]
    rng = random.Random(6503)
    tokens = ["[", "]", "(", ")", "#session-", "#note-", "#event-", "a", "1", "-", " "]
    cases += ["".join(rng.choices(tokens, k=28)) for _ in range(3000)]
    for text in cases:
        expected = reference.findall(text)
        actual = [(label, target) for _start, _end, label, target in iter_markdown_links(
            text, target_prefix=prefix, target_re=validator
        )]
        assert actual == expected, text

    generic = re.compile(r"\[([^\]]+)\]\([^)]+\)")
    for text in cases:
        assert replace_markdown_links_with_labels(text) == generic.sub(r"\1", text), text

    angle = re.compile(r"<([^>]+)>")
    for text in cases + ["<x>", "<<x>", "<>", "<a><b>", "<<<"]:
        assert [content for _start, _end, content in iter_angle_contents(text)] == angle.findall(text)


def test_numbered_row_identifier_scan_matches_legacy_rows():
    pattern = re.compile(r"^\s*\d+\.\s+.+?\s+\(([^)\n]+)\)\s+[—-]", re.M)
    cases = [
        "1. Task (abc) — due", "  2. Label ((nested) - tail", "1. no id",
        "1. a (bad) x (good) — tail", "\n\n3. item (id-3) - tail",
    ]
    rng = random.Random(6503)
    tokens = ["1. ", "x", " ", "(", ")", " -", " —", "\n"]
    cases += ["".join(rng.choices(tokens, k=20)) for _ in range(2000)]
    for text in cases:
        assert _numbered_row_parenthesized_ids(text) == pattern.findall(text), text


def test_model_and_request_extractors_match_legacy_grammars():
    youtube = re.compile(
        r"\b(?:youtube|youtu\.be|yt|video\s+comments?|comments?\s+on\s+(?:the\s+)?video|"
        r"transcript\s+(?:of|for)|(?:latest|newest|recent)\s+(?:\d+\s+)?(?:videos?|uploads?)|"
        r"official\s+.+\s+channel)\b", re.I,
    )
    destructive = re.compile(r"\b(delete|remove|archive|trash|send|reply|unsubscribe|mark\s+.*read)\b", re.I)
    terse = re.compile(
        r"\s*(?:(?:send|sned|share|give|show)?\s*(?:me\s+)?(?:the\s+)?(?:links?|urls?|sources?)"
        r"(?:\s+(?:for|to|from)\s+(?:those|that|them|these|it|this|the\s+(?:sites?|websites?|resources?|sources?)))?"
        r"|(?:for|to|from)\s+(?:those|that|them|these|it|this|the\s+(?:sites?|websites?|resources?|sources?)))"
        r"\s*(?:please|pls)?[.!?]?\s*",
    )
    cases = ["official project channel", "official x channel", "mark all as read", "mark\nread",
             " send me the links please ", "for those sites", "plain text"]
    rng = random.Random(6503)
    tokens = ["official", "channel", "mark", "read", "send", "links", "for", "those", "sites", " ", "\n", "x"]
    cases += ["".join(rng.choices(tokens, k=24)) for _ in range(3000)]
    for text in cases:
        assert _looks_like_youtube_tool_turn(text) == bool(youtube.search(text)), text
        assert _looks_like_destructive_request(text) == bool(destructive.search(text)), text
        assert _is_terse_link_request(text) == bool(terse.fullmatch(text.lower())), text

    summary_re = re.compile(
        r"\bsummary\s*(?:\*\*)?\s*:?\s*(.+?)(?:\n\s*(?:-|\\*\\*|If you|Want me|This is|$))",
        re.I | re.S,
    )
    summary_cases = ["Summary: useful\n- next", "summary **: x\n\nWant me to continue", "summary: no end"]
    for text in summary_cases:
        match = summary_re.search(text)
        assert _contextual_summary_fragment(text) == (match.group(1) if match else "")

    product_re = re.compile(
        r"\b(?:find|look\s+for|shop\s+for|search\s+for)\s+(?:me\s+)?(?:the\s+)?(?:best\s+)?"
        r"(?P<query>.+?)\s*[?.!]*$", re.I,
    )
    for text in ["find best camera???", "look for me the best shoes", "shop for ???", "plain"]:
        normalized = re.sub(r"\s+", " ", text).strip()
        match = product_re.search(normalized)
        expected = ""
        if match:
            expected = match.group("query").strip(" \t\r\n.,!?;:")
            expected = re.sub(
                r"\s+(?:on|at|from)\s+(?:the\s+)?[A-Za-z0-9&.' -]{1,60}$", "", expected, flags=re.I
            ).strip()
            expected = expected[:120] if 0 < len(expected.split()) <= 12 else ""
        assert _private_browser_product_query(text) == expected

    title_re = re.compile(r"<title(?:\s[^>]*)?>([\s\S]*?)</title>", re.I)
    for text in ["<title>x</title>", "<TITLE class='x'>a<b</title>", "<title", "<titlex>x</titlex>"]:
        match = title_re.search(text)
        assert first_tag_content(text, "title", allow_attributes=True) == (match.group(1) if match else None)


def test_summary_capture_preserves_exact_head_whitespace_and_newline_grammar():
    legacy = re.compile(
        r"\bsummary\s*(?:\*\*)?\s*:?\s*(.+?)(?:\n\s*(?:-|\\*\\*|If you|Want me|This is|$))",
        re.I | re.S,
    )
    cases = [
        "Summary: useful\nordinary next line\n- next",
        "Summary: useful\nx\n", "summary\n\n", "summary \n",
        "summary: \n", "summary **:\n\n", "summary:\nX\n",
        "summary summary: X\n", "summary: no newline",
    ]
    rng = random.Random(6509)
    tokens = ["summary", "Summary", ":", "**", "\\", " ", "\t", "\n", "x", "-", "If you"]
    cases += ["".join(rng.choices(tokens, k=24)) for _ in range(5000)]
    for text in cases:
        match = legacy.search(text)
        assert _contextual_summary_fragment(text) == (match.group(1) if match else ""), text


def test_repeated_listing_words_preserve_legacy_request_grammar():
    legacy = re.compile(
        r"\s*(?:top|latest|recent|list(?: the)?|show(?: me)?(?: the)?)\s+"
        r"(?:[\w .:/-]+\s+)?(?:stories|articles|posts|headlines|pages)"
        r"(?:\s+on\s+[\w .:/-]+)?[.!?]?\s*", re.I,
    )
    for text in ("top Straße stories", "top İ stories", "lİst stories", "top storİes",
                 "top café stories", "top stories on Straße", "top stories on İ"):
        assert _page_listing_request(text) == bool(legacy.fullmatch(text)), text
    for word in ("stories", "articles", "posts", "headlines", "pages"):
        for count in (1, 2, 8, 32):
            for suffix in ("X", "@", "\tX", "on x", "on \t", "on  ", "\n"):
                for separator in (" ", " on ", "\t", " \t"):
                    text = "top " + (word + separator) * count + suffix
                    assert _page_listing_request(text) == bool(legacy.fullmatch(text)), text


def test_repeated_navigation_schemes_preserve_legacy_url_search():
    for count in (1, 2, 8, 32):
        for suffix in ("X", "google.com/search", "bing.com/html", "duckduckgo.com/?q=x"):
            text = "http://" * count + suffix
            assert contains_search_engine_navigation(text) == bool(_SEARCH_ENGINE_URL.search(text)), text


def test_tool_listing_rows_match_legacy_grammars():
    research = re.compile(r"^-\s+\[(.*?)\]\(#research-([^)]+)\)(.*)$")
    skills = re.compile(r"^-\s+\*\*(.*?)\*\*(?:\s+\((.*?)\)|\s+\[(draft)\])?(?::\s*(.*))?$")
    calendar = re.compile(r"^\s*-\s+(.+?):\s+\[(.*?)\]\(#event-([^)]+)\)(.*)$")
    draft = re.compile(r"\bTo:\s*.+\bSubject:\s*.+\n---", re.I | re.S)
    cases = [
        "- [Title](#research-id) tail",
        "- **name** (published): description",
        "- **name** [draft]",
        " - when: [title](#event-id) tail",
        "To: a\nSubject: b\n---",
        "To:Subject:x\n---",
        "plain",
    ]
    rng = random.Random(6504)
    tokens = ["-", " ", "\t", "[", "]", "(", ")", "*", ":", "#research-", "#event-", "draft", "x"]
    cases += ["".join(rng.choices(tokens, k=28)) for _ in range(5000)]
    for text in cases:
        match = research.match(text)
        assert _research_listing_row(text) == (match.groups() if match else None), text
        match = skills.match(text)
        assert _skill_listing_row(text) == (match.groups() if match else None), text
        match = calendar.match(text)
        assert _calendar_listing_row(text) == (match.groups() if match else None), text
        assert _contains_email_draft_headers(text) == bool(draft.search(text)), text


def test_terse_email_followup_matches_legacy_grammar():
    legacy = re.compile(
        r"^\s*(?:and|so|well|still|then|okay|ok|did you find it(?: yet)?|what did you find)\s*[?.!]*\s*$",
        re.I,
    )
    rng = random.Random(6508)
    tokens = ["and", "so", "well", "did you find it", " yet", "what did you find", " ", "\t", "?", ".", "!", "x"]
    cases = ["and?", "  did you find it yet ! ", "what did you find", "and   X"]
    cases += ["".join(rng.choices(tokens, k=20)) for _ in range(3000)]
    for text in cases:
        assert _is_terse_email_lookup_followup(text) == bool(legacy.match(text)), text


def test_preview_request_and_source_scans_match_legacy_grammars():
    page = re.compile(
        r"\s*(?:top|latest|recent|list(?: the)?|show(?: me)?(?: the)?)\s+"
        r"(?:[\w .:/-]+\s+)?(?:stories|articles|posts|headlines|pages)"
        r"(?:\s+on\s+[\w .:/-]+)?[.!?]?\s*", re.I,
    )
    prior = re.compile(
        r"\s*(?:(?:where|what)\s+did\s+you\s+(?:get|find)\s+(?:that|this)\s+from[?., ]*"
        r"(?:give|show|send)\s+me\s+(?:the\s+)?(?:source\s+)?link[.!? ]*"
        r"|(?:give|show|send)\s+me\s+(?:the\s+)?(?:source\s+)?link(?:\s+for\s+that)?[.!? ]*"
        r"|what(?:['’]?s|\s+is)\s+(?:the\s+)?source(?:\s+link)?[.!? ]*)\s*", re.I,
    )
    terminal = re.compile(
        r"(?:^|[.!?;,\n])\s*(?:(?:pls|please)\s+)?(?:sources?|citations?|links?)"
        r"\s*(?:pls|please)?\s*[.!?]*$", re.I,
    )
    rows = re.compile(r"^\[\d+\]\s+(.+?)\s*\n\s*(https?://\S+)", re.M)
    cases = [
        "top stories", "show me the latest stories on example.com", "give me the source link",
        "what's the source?", "x. please links pls!!", "[1] Title\nhttps://example.test/x", "plain",
    ]
    rng = random.Random(6505)
    tokens = ["top", "show", " me", " the", " stories", " on", "link", "source", "please", " ", "\t", ".", "!", "\n", "[1]", "http://x"]
    cases += ["".join(rng.choices(tokens, k=22)) for _ in range(5000)]
    for text in cases:
        assert _page_listing_request(text) == bool(page.fullmatch(text)), text
        assert _prior_web_source_request(text) == bool(prior.fullmatch(text)), text
        assert _terminal_source_link_clause(text) == bool(terminal.search(text)), text
        assert _web_source_rows(text) == rows.findall(text), text


def test_staged_command_payload_parsers_match_legacy_grammars():
    specs = [
        (
            re.compile(r"\bnote\s+titled\s+(.+?)\s+so\s+its\s+content\s+is\s+['\"]([^'\"]+)['\"]", re.I),
            r"\bnote\s+titled(?=\s)",
            r"\s+(.+?)\s+so\s+its\s+content\s+is\s+['\"]([^'\"]+)['\"]",
        ),
        (
            re.compile(r"\b(?:delete|trash|remove|archive|mark(?:\s+as)?\s+(?:read|unread)|mark\s+(?:read|unread))\b\s+(?:all|every|the)?\s*(?:my\s+)?(.+?)\s+(?:emails?|mail|messages?)\b", re.I),
            r"\b(?:delete|trash|remove|archive|mark(?:\s+as)?\s+(?:read|unread)|mark\s+(?:read|unread))\b(?=\s)",
            r"\s+(?:all|every|the)?\s*(?:my\s+)?(.+?)\s+(?:emails?|mail|messages?)\b",
        ),
        (
            re.compile(r"\b(?:make|create|add)\s+(?:a\s+)?checklist\s+(?:called|titled|named)\s+(.+?)\s+with\s+(.+?)\s*$", re.I),
            r"\b(?:make|create|add)\s+(?:a\s+)?checklist\s+(?:called|titled|named)(?=\s)",
            r"\s+(.+?)\s+with\s+(.+?)$",
        ),
        (
            re.compile(r"\b(?:change|update|set|retag)\b\s+(?:the\s+)?(.+?)\s+tag\s+to\s+#?([a-z][a-z0-9_-]{1,30})\b", re.I),
            r"\b(?:change|update|set|retag)\b(?=\s)",
            r"\s+(?:the\s+)?(.+?)\s+tag\s+to\s+#?([a-z][a-z0-9_-]{1,30})\b",
        ),
        (
            re.compile(r"\b(?:chang(?:e|es|ed|ing)|updat(?:e|es|ed|ing))\s+(.+?)\s+to\s+(.+?)(?=\s+(?:in|and|then|before)\b|[.;]|$)", re.I),
            r"\b(?:chang(?:e|es|ed|ing)|updat(?:e|es|ed|ing))(?=\s)",
            r"\s+(.+?)\s+to\s+(.+?)(?=\s+(?:in|and|then|before)\b|[.;]|$)",
        ),
        (
            re.compile(r"\breplace\s+(.+?)\s+with\s+(.+?)(?=\s+(?:in|and|then|before)\b|[.;]|$)", re.I),
            r"\breplace(?=\s)",
            r"\s+(.+?)\s+with\s+(.+?)(?=\s+(?:in|and|then|before)\b|[.;]|$)",
        ),
    ]
    pipeline = re.compile(
        r"\bpipeline\s+using\s+([^\s,]+)\s+to\s+(.+?),\s*then\s+([^\s,]+)\s+to\s+(.+?)(?:[.!?]\s*)?$",
        re.I,
    )
    session = re.compile(r"\b(?:find|search(?:\s+for)?|show)\s+(?:the\s+)?(.+?)\s+(?:chat|session|conversation)\b", re.I)
    checklist = re.compile(r"\b(?:what(?:'s| is)?|show|tell\s+me)\b.*?\b(?:left|remaining)\b.*?\b(?:on|in)\s+(?:the\s+)?(.+?)\s+checklist\b", re.I)
    rng = random.Random(6506)
    tokens = ["note", " titled", " so its content is ", "'x'", "delete", " all", " emails", "create checklist called", " with", "change", " tag to ", "replace", " to", " pipeline using ", " then", "find", " chat", "left", " on", " checklist", " ", "\t", ".", "x"]
    cases = ["update note titled a so its content is 'b'", "delete all all emails", "create checklist called a with b", "change the trip tag to work", "replace old with new", "pipeline using a to x, then b to y", "find the chat", "what is left on the trip checklist"]
    cases += ["".join(rng.choices(tokens, k=25)).strip() for _ in range(5000)]
    for text in cases:
        for legacy, prefix, remainder in specs:
            match = legacy.search(text)
            assert _captures_after_first_prefix(text, prefix, remainder) == (match.groups() if match else None), (legacy.pattern, text)
        match = pipeline.search(text)
        assert _pipeline_request_parts(text) == (match.groups() if match else None), text
        match = session.search(text)
        assert _session_find_query_capture(text) == (match.group(1) if match else None), text
        match = checklist.search(text)
        assert _remaining_checklist_name(text) == (match.group(1) if match else None), text


def test_turn_contract_scans_match_legacy_grammars():
    cancelled = re.compile(
        r"^(?:never\s*mind|scratch\s+that)\s*[,;:—–-]?\s*(?=(?:open|show|list|read|search|find|check|switch|go)\b)",
        re.I,
    )
    prohibition = re.compile(
        r"^\s*(?:read[- ]only(?:\s+and)?\s+)?(?:do\s+not|don['’]?t|never)\s+"
        r"(?:add|create|make|write|draft|edit|change|update|delete|remove|send|reply|run|execute|download|serve|open|save|schedule|transcribe|inspect)\b"
        r"[^.;\n]*[.!?]*\s*$", re.I,
    )
    budget = re.compile(r"\bunder\s+[¥$€£]?\s*\d+(?:[.,]\d+)?(?:\s*yen)?\b", re.I)
    terminal_specs = [
        r"[,.;?]\s*(?:(?:only|just)\s+)?(?:(?:list|show)\s+(?:me\s+)?)?a\s+few(?:\s+(?:task\s+)?(?:names?|items?|results?|entries?))?(?:\s+and\s+(?:whether|if)\s+[^.;\n]+)?",
        r"[.;]\s*read[- ]only(?:\s+(?:please|pls|plz))?\s*,?\s*(?:(?:and\s+)?(?:do\s+not|don['’]?t|dont)\s+(?:change|edit|modify)(?:\s+or\s+send)?\s+(?:anything|data))?",
        r"(?:(?:[,;]\s*(?:and\s+)?|\s+and\s+))?(?:do\s+not|don['’]?t|dont)\s+(?:touch|change|edit|modify)(?:\s+(?:anything|data|them))?(?:\s+yet)?",
        r"(?:(?:[,;]\s*(?:and\s+)?|\s+and\s+))?no\s+changes?",
        r"[,;]\s*(?:keep\s+(?:them|it)\s+)?short\s+lines?\s*,?",
    ]
    rng = random.Random(6507)
    tokens = ["never", " mind", "scratch", " that", "open", "do not", " touch", " anything", "no changes", "read-only", "a few", " and whether", "short lines", "under", "$", "123", "yen", "open my calendar", "and", " what is that", " ", "\t", ".", "!", ",", ";", "x"]
    cases = ["never mind, open calendar", "do not edit anything.", "under $ 20 yen", "open my calendar", "open calendar and what is next", "x but   "]
    cases += ["".join(rng.choices(tokens, k=22)) for _ in range(5000)]
    for text in cases:
        assert _strip_cancelled_request_lead(text) == cancelled.sub("", text), text
        assert _is_pure_action_prohibition(text) == bool(prohibition.fullmatch(text)), text
        assert _mentions_under_budget(text) == bool(budget.search(text)), text
        assert _strip_terminal_but(text) == re.sub(r"\s+but\s*$", "", text, flags=re.I), text
        for core in terminal_specs:
            legacy = re.search(core + r"[.!?]*\s*$", text, re.I)
            current = _terminal_clause_match(text, core)
            assert (current.start() if current else None) == (legacy.start() if legacy else None), (core, text)
        match = _WARM_RECALL.fullmatch(text)
        assert _warm_recall_parts(text) == ((match.group("target"), "") if match else None), text
        match = _WARM_RECALL_WITH_FOLLOWUP.fullmatch(text)
        assert _warm_recall_parts(text, with_followup=True) == (
            (match.group("target"), match.group("followup")) if match else None
        ), text


@pytest.mark.parametrize("program", [
    r'''
from src.agent_loop import _contextual_summary_fragment
assert _contextual_summary_fragment("Summary: x\n" + "x\n" * 100_000) == "x"
assert _contextual_summary_fragment("Summary:" + "\n" * 100_000) == "\n"
''',
    r'''
from src.clean_agent_preview import _page_listing_request
for text in ("top " + "stories " * 30_000 + "X",
             "top " + "stories on " * 30_000 + "@"):
    assert not _page_listing_request(text)
''',
    r'''
from src.text_scanning import contains_search_engine_navigation
assert not contains_search_engine_navigation("http://" * 100_000 + "X")
assert contains_search_engine_navigation("http://" * 100_000 + "bing.com/search")
''',
    r'''
from src.agent_loop import _is_terse_email_lookup_followup
_is_terse_email_lookup_followup("and" + " " * 100_000 + "X")
''',
    r'''
from src.turn_contract import (_is_pure_action_prohibition, _mentions_under_budget,
    _strip_cancelled_request_lead, _terminal_clause_match, _warm_recall_parts)
space = " " * 100_000
_strip_cancelled_request_lead("never" + space + "mind" + space + "X")
_terminal_clause_match(", a few and whether " + space + "X;", r"[,.;?]\s*a\s+few(?:\s+and\s+whether\s+[^.;\n]+)?")
_is_pure_action_prohibition("do not add " + space + "X;")
_mentions_under_budget("under" + space + "$" + space + "X")
_warm_recall_parts("open" + space + "my" + space + "calendar" + space + "X")
_warm_recall_parts("open" + space + "my" + space + "calendar" + space + "and" + space + "what " + "x" * 181, with_followup=True)
''',
    r'''
from src.agent_loop import (_captures_after_first_prefix, _pipeline_request_parts,
    _remaining_checklist_name, _session_find_query_capture)
evil = ("delete all x " * 30_000) + "z"
_captures_after_first_prefix(evil, r"\bdelete\b(?=\s)", r"\s+(?:all)?\s*(.+?)\s+emails?\b")
_pipeline_request_parts(("pipeline using m to x " * 30_000) + "z")
_session_find_query_capture(("find x " * 30_000) + "z")
_remaining_checklist_name("what " + ("left on " * 30_000) + "z")
''',
    r'''
from src.clean_agent_preview import (_page_listing_request, _prior_web_source_request,
    _terminal_source_link_clause, _web_source_rows)
_page_listing_request("top stories on " + " " * 100_000 + "\nX")
_prior_web_source_request("give me link" + " " * 100_000 + "X")
_terminal_source_link_clause("links" + " " * 100_000 + "X")
_web_source_rows("[1] " + " " * 100_000)
''',
    r'''
from src.agent_loop import (_calendar_listing_row, _contains_email_draft_headers,
    _research_listing_row, _skill_listing_row)
for text in (
    "- [" + "](#research-" * 30_000,
    "- **" + " **" * 30_000,
    "- when: [" + "](#event-" * 30_000,
    "To: x " + "Subject: x " * 30_000,
):
    _research_listing_row(text)
    _skill_listing_row(text)
    _calendar_listing_row(text)
    _contains_email_draft_headers(text)
''',
    r'''
from src.agent_loop import _session_link_from_row, _session_list_summary_from_tool_output, _strip_trailing_done
evil = "Done. " + "\t" * 100_000 + "x"
assert _strip_trailing_done(evil) == evil
for row in ("[" + "\\" * 100_000, "[" * 100_000,
            "[a" + "\\](#session-" * 20_000 + ")"):
    _session_link_from_row(row)
for row in ("[x](#session-id) (" + "msgs" * 100_000,
            "[x](#session-id) (" + "(last active today)" * 20_000):
    _session_list_summary_from_tool_output("Chats:\n- " + row)
''',
    r'''
from src.tool_parsing import *
from src.tool_parsing import (_iter_named_blocks, _strip_delimited,
    _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE)
text = "}<|tool_call|>" + "<|tool_call|>call:web_search{" * 20_000
assert list(_iter_named_blocks(text, _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE)) == []
assert _strip_delimited(text, _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE) == text
# Public entry points also stay responsive, including fallback parsers.
text = "}<|tool_call|>" + "<|tool_call|>call:web_search{" * 3000
assert parse_tool_blocks(text) == []
strip_tool_blocks(text)
''',
    r'''
from src.tool_parsing import (_iter_named_blocks, _iter_qwen_python_args,
    _QWEN_FUNCTION_OPEN_RE, _QWEN_FUNCTION_CLOSE_RE,
    _QWEN_PARAMETER_OPEN_RE, _QWEN_PARAMETER_CLOSE_RE, parse_tool_blocks)
for opener, closer, text in (
    (_QWEN_FUNCTION_OPEN_RE, _QWEN_FUNCTION_CLOSE_RE, "<function=manage_notes>" * 20_000),
    (_QWEN_PARAMETER_OPEN_RE, _QWEN_PARAMETER_CLOSE_RE, "<parameter=action>" * 20_000),
    (_QWEN_FUNCTION_OPEN_RE, _QWEN_FUNCTION_CLOSE_RE, "<function=manage_notes>a" + "\t" * 100_000 + "x"),
):
    assert list(_iter_named_blocks(text, opener, closer)) == []
assert list(_iter_qwen_python_args("a" * 100_000)) == []
assert parse_tool_blocks("<function=manage_notes>" * 3000) == []
assert parse_tool_blocks("manage_notes(" + "a" * 100_000 + ")")
''',
    r'''
from src.tool_parsing import strip_angle_tags
for allow_empty in (False, True):
    for replacement in ("", " "):
        for text in ("<" * 200_000, ">" + "<" * 200_000):
            assert strip_angle_tags(text, replacement, allow_empty=allow_empty) == text
        assert strip_angle_tags("<" * 200_000 + ">tail", replacement,
                                allow_empty=allow_empty) == replacement + "tail"
''',
    r'''
from src.tool_parsing import iter_email_addresses
for ascii_only in (False, True):
    for text in ("+" * 100_000, "a@" + "a" * 100_000,
                 "a@" + "." * 100_000, "a@" * 20_000):
        assert list(iter_email_addresses(text, ascii_only=ascii_only)) == []
''',
    r'''
from src.agent_loop import _mentions_pdf_url, _mentions_workspace_script
from src.clean_agent_preview import declared_workspace_artifacts
from src.text_scanning import contains_search_engine_navigation
from src.turn_contract import _mentions_workspace_artifact, _mentions_workspace_output
workspace = "/workspace/" * 30_000 + "x"
assert not _mentions_workspace_script(workspace)
assert not _mentions_workspace_artifact(workspace)
assert not _mentions_workspace_output(workspace)
assert declared_workspace_artifacts("create " + workspace) == ()
assert not _mentions_pdf_url("http://" * 30_000 + "x")
assert not contains_search_engine_navigation("http://google." + "..google." * 30_000 + "x")
''',
    r'''
from src.agent_loop import _looks_like_agent_reasoning_preamble, _looks_like_ody_qwen_leaked_tool_text
from src.text_scanning import contains_detailed_sequence_request
for text in ("\n" * 100_000 + "x", ("when " * 20_000) + "x"):
    _looks_like_agent_reasoning_preamble(text)
    _looks_like_ody_qwen_leaked_tool_text(text)
    contains_detailed_sequence_request(text)
''',
    r'''
from src.agent_loop import (_email_account_label, _email_sender_name,
    _parse_explicit_open_panel_request, _read_only_shell_command,
    _split_before_assistant_prompt, _split_note_items,
    _strip_horizontal_space_before_lf)
space = " " * 100_000
_parse_explicit_open_panel_request("open calendar" + space + "x")
_split_note_items(space + "x")
_email_sender_name("<" * 100_000 + "x")
_email_account_label("<" * 100_000 + "x")
_split_before_assistant_prompt("\n" * 100_000 + "x")
_strip_horizontal_space_before_lf(" " * 100_000 + "x")
_read_only_shell_command(space + "x")
''',
    r'''
from src.agent_loop import _numbered_row_parenthesized_ids
from src.text_scanning import iter_angle_contents, iter_markdown_links, replace_markdown_links_with_labels
for text in ("<" * 100_000, "[" * 100_000,
             ("[x](#session-" * 20_000) + "missing"):
    list(iter_angle_contents(text))
    list(iter_markdown_links(text, target_prefix="#session-"))
    replace_markdown_links_with_labels(text)
_numbered_row_parenthesized_ids("1. x " + "(" * 100_000 + "x")
''',
    r'''
from src.agent_loop import (_contextual_summary_fragment, _is_terse_link_request,
    _looks_like_destructive_request, _looks_like_youtube_tool_turn,
    _private_browser_product_query)
from src.text_scanning import first_tag_content
for text in (("official " * 30_000) + "x", ("mark " * 30_000) + "x",
             " " * 100_000 + "x", ("summary: x " * 20_000) + "x",
             "find best " + "?" * 100_000 + "x", "<title" * 30_000):
    _looks_like_youtube_tool_turn(text)
    _looks_like_destructive_request(text)
    _is_terse_link_request(text)
    _contextual_summary_fragment(text)
    _private_browser_product_query(text)
    first_tag_content(text, "title", allow_attributes=True)
''',
])
def test_hostile_parsers_complete_with_a_short_process_deadline(program):
    # A regression cannot wedge pytest: the subprocess is killed at the limit.
    # This includes import overhead; fixed scans themselves take milliseconds.
    subprocess.run([sys.executable, "-c", textwrap.dedent(program)],
                   check=True, timeout=8, capture_output=True, text=True)
