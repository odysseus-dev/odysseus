"""Closure regressions for production CodeQL alerts #1110 through #1129."""

import ast
import itertools
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.agent_loop import (
    _contextual_email_subject,
    _looks_like_agent_reasoning_preamble,
    _payload_fields,
    _pipeline_request_parts,
)
from src.text_scanning import iter_prefixed_token_matches
from src.tool_parsing import _iter_gemma_fallback_items, _parse_gemma_tool_call


PAYLOADS = {
    "note_update": r'''\s+(.+?)\s+so\s+its\s+content\s+is\s+['"]([^'"]+)['"]''',
    "email_mutation": r"\s+(?:all|every|the)?\s*(?:my\s+)?(.+?)\s+(?:emails?|mail|messages?)\b",
    "checklist": r"\s+(.+?)\s+with\s+(.+?)$",
    "tag": r"\s+(?:the\s+)?(.+?)\s+tag\s+to\s+#?([a-z][a-z0-9_-]{1,30})\b",
    "change": r"\s+(.+?)\s+to\s+(.+?)(?=\s+(?:in|and|then|before)\b|[.;]|$)",
    "replace": r"\s+(.+?)\s+with\s+(.+?)(?=\s+(?:in|and|then|before)\b|[.;]|$)",
}


@pytest.mark.parametrize("grammar,pattern", PAYLOADS.items())
def test_payload_fields_preserve_whitespace_and_optional_leader_priority(grammar, pattern):
    legacy = re.compile(pattern, re.I)
    spaces = [" ", "\t", "\n", "\r", "  ", "\t\n\t", "\n\n\n", "\u2003"]
    words = ["", "x", "all", "my", "the", "with", "to"]
    ends = [" emails", " checklist", " so its content is 'v'", " with y", " to y", " tag to ab"]
    cases = [a + b + a + c for a, b, c in itertools.product(spaces, words, ends)]
    rng = random.Random(1117)
    tokens = spaces + words + ends + ["in", "and", ".", ";", "'v'"]
    cases += ["".join(rng.choices(tokens, k=15)) for _ in range(5000)]
    for text in cases:
        match = legacy.match(text)
        assert _payload_fields(text, grammar) == (match.groups() if match else None), text


def test_pipeline_fields_preserve_empty_looking_captures():
    pipeline = re.compile(
        r"\bpipeline\s+using\s+([^\s,]+)\s+to\s+(.+?),\s*then\s+([^\s,]+)\s+to\s+(.+?)(?:[.!?]\s*)?$", re.I,
    )
    for space, first, second in itertools.product(
        [" ", "  ", "\t\n\t", "\n", "\r"], ["x", "", ",then n to", "!"], ["x", "", "!", "! ", "X\n"],
    ):
        text = "pipeline using m to" + space + first + ",then n to" + space + second
        match = pipeline.search(text)
        assert _pipeline_request_parts(text) == (match.groups() if match else None), text


def test_gemma_fallback_preserves_permissive_values_and_multiline_failures():
    legacy = re.compile(r'''(\w+)\s*:\s*["']?(.*?)["']?(?=\s*,\s*\w+\s*:|\s*\})''')
    cases = ["{query: hello, x: 'world'}", "{query:'a,b:c'}", "a:\n\t}", "0:\t\tX", "a:'', b: }", "a: multiline\nfails, b: ok}"]
    rng = random.Random(1128)
    tokens = ["a", "b", "0", ":", ",", "}", "'", '"', " ", "\t", "\n", "\u2003"]
    cases += ["".join(rng.choices(tokens, k=40)) for _ in range(15000)]
    for text in cases:
        expected = [(match.group(1), match.group(2).strip()) for match in legacy.finditer(text)]
        assert list(_iter_gemma_fallback_items(text)) == expected, text
    block = _parse_gemma_tool_call("web_search", "{query: hello world}")
    assert block.tool_type == "web_search"
    assert block.content == "hello world"


def test_subject_capture_preserves_optional_formatting_and_whitespace():
    legacy = re.compile(r'''\bsubject\s*(?:\*\*)?\s*:?\s*(?:"|\*")?(.+?)(?:"|\n|$)''', re.I)
    rng = random.Random(1123)
    tokens = ["subject", "subjectx", "**", ":", '*"', '"', "a", " ", "\t", "\n", "\r"]
    cases = ["Subject:", "Subject:\t", "Subject**:\n\n", "Subject \t\nTitle", "Subject\n\n", 'subject""']
    cases += ["".join(rng.choices(tokens, k=16)) for _ in range(10000)]
    for text in cases:
        match = legacy.search(text)
        assert _contextual_email_subject(text) == (match.group(1) if match else None), text


def _regex_literals(path):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / path).read_text())
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_terminal_read_only_removal_preserves_comma_and_whitespace_behavior():
    legacy = (
        r"[.!?]\s*read[- ]only(?:\s+(?:please|pls|plz))?\s*,?\s*"
        r"(?:do\s+not|don['’]?t|dont)\s+(?:change|edit|modify)\s+"
        r"(?:or\s+send\s+)?anything[.!?]*\s*$"
    )
    current = next(p for p in _regex_literals("src/turn_contract.py") if p.startswith(r"[.!?]\s*+read[- ]only"))
    for space, optional, comma, ending in itertools.product(
        ["", " ", "\t", "\n\t"], ["", " please", " pls"], ["", ","], ["", ".", "!?", "X"],
    ):
        text = "open calendar!read only" + optional + space + comma + space + "do not edit anything" + ending
        assert re.sub(current, "", text, flags=re.I) == re.sub(legacy, "", text, flags=re.I), text


def test_bounded_session_listing_and_reasoning_tail_preserve_regex_behavior():
    literals = _regex_literals("src/agent_loop.py")
    listing = next(p for p in literals if p.startswith(r"\b(?:list|show|view)\b.{0,30}") and "my" in p)
    old_listing = listing.replace(r"\s++", r"\s+").replace(r"\s*+", r"\s*")
    preamble = next(p for p in literals if p.startswith(r"(?:but\s+)?(?:now\s+)?") and "[^.!?]*+" in p)
    old_preamble = preamble.replace("[^.!?]*+", "[^.!?]*")
    rng = random.Random(1119)
    tokens = ["list", "my", "recent", "chat", "now let me verify", " ", "\t", "\n", ".", "!", "x"]
    cases = ["".join(rng.choices(tokens, k=15)) for _ in range(3000)]
    for text in cases:
        assert bool(re.search(listing, text, re.I)) == bool(re.search(old_listing, text, re.I)), text
        assert bool(re.match(preamble, text)) == bool(re.match(old_preamble, text)), text
    assert not _looks_like_agent_reasoning_preamble("Answer. now let me verify\t\t!!")


def test_calendar_digit_guard_prevents_suffix_retries_without_changing_units():
    patterns = [p for p in _regex_literals("src/tools/calendar.py") if p.startswith(r"(?<!\d)(\d+)\s*")]
    assert len(patterns) == 4
    for pattern in patterns:
        legacy = pattern.replace(r"(?<!\d)", "")
        for value in ("10 minutes", "1hr30m", "９ hours", "12\tmin", "0" * 2000 + "X"):
            match = re.search(pattern, value)
            old = re.search(legacy, value)
            assert (match.groups() if match else None) == (old.groups() if old else None)


def test_prefixed_token_helper_tests_only_one_failed_prefix_per_token():
    class Counted:
        def __init__(self):
            self.calls = 0
            self.regex = re.compile(r"/workspace/[^\s]+\.txt\b")
        def match(self, text, pos):
            self.calls += 1
            return self.regex.match(text, pos)
    parser = Counted()
    flood = "/workspace/" * 10000 + "X"
    assert list(iter_prefixed_token_matches(flood, re.compile(r"/workspace/"), parser, re.compile(r"\S*"))) == []
    assert parser.calls == 1


@pytest.mark.parametrize("program", [
    "from src.agent_loop import _looks_like_agent_reasoning_preamble as f; assert not f('Answer. now let me verify ' + '\\t'*100000 + '!!')",
    "from src.agent_loop import _payload_fields; [ _payload_fields('\\t'*100000+'X', g) for g in ('note_update','email_mutation','checklist','tag','change','replace','remaining_checklist') ]",
    "from src.agent_loop import _payload_fields; assert _payload_fields(' a' + ' to x'*30000 + '\\nX', 'change') is None",
    "from src.agent_loop import _pipeline_request_parts as f; assert f('pipeline using m to'+'\\t'*100000+'X') is None",
    "from src.agent_loop import _parse_qwen_explicit_session_find as f; f('list my'+'\\t'*100000+'X chats')",
    "from src.agent_loop import _parse_simple_notes_tool_request as f; f('create note saying'+'\\t'*100000+'X\\nY')",
    "from src.agent_loop import _contextual_email_subject as f; assert f('subject'+'\\n'*100000) is None",
    "from src.agent_loop import _looks_like_ody_qwen_leaked_tool_text as f; assert not f('<'+'\\t'*100000+'X')",
    "from src.tool_parsing import _iter_gemma_fallback_items as f; assert list(f('0'*100000+'X')) == []; assert list(f('0:'+'\\t'*100000+'X')) == []",
    "from src.tool_parsing import _iter_gemma_fallback_items as f; assert len(list(f('a:x,'*50000+'b:y}'))) == 50001",
    "from src.turn_contract import _read_request_and_limit as f; f('open calendar!read only'+'\\t'*100000+'X')",
])
def test_induced_defects_complete_under_process_deadline(program):
    subprocess.run([sys.executable, "-c", program], check=True, timeout=8, capture_output=True, text=True)


def test_read_only_reproducer_related_limit_prefixes_preserve_matches():
    literals = _regex_literals("src/turn_contract.py")
    natural = next(p for p in literals if p.startswith(r"(?:[,.;?]|[—–-]|(?<!\s)\s++but"))
    conversational = next(p for p in literals if p.startswith(r"(?:[,.;?]\s*|(?<!\s)\s++)"))
    rng = random.Random(1129)
    tokens = ["open calendar", "read only", " ", "\t", "\n", ",", "but", "only", "need", "just", "first", "3", "titles", "and", "status", "maybe", "keep it to", "top", "short"]
    cases = ["open calendar only 3", "open calendar but only need 3 titles", "open calendar keep it to 3", "x only\t\tX"]
    cases += ["".join(rng.choices(tokens, k=15)) for _ in range(5000)]
    # The full pattern includes the count expression concatenated between
    # literals. Obtain it from the real production search call, rather than
    # testing only the first source-code fragment.
    from src import turn_contract
    counts = turn_contract._READ_COUNT
    natural_tail = (
        r"(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?|bits?|things?))?"
        r"(?:\s*(?:and|\+)\s+(?:their\s+)?(?:status(?:es)?|states?))?"
        r"(?:\s*(?:\+|and)\s+(?:whether|if)\s+[^.;\n]+)?[.!?]*\s*$"
    )
    conversational_tail = r")(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?))?[.!?]*\s*$"
    patterns = [natural + counts + r")" + natural_tail, conversational + counts + conversational_tail]
    for current in patterns:
        legacy = current.replace(r"(?<!\s)", "").replace(r"\s++", r"\s+").replace(r"\s*+", r"\s*")
        for text in cases:
            new = re.search(current, text, re.I)
            old = re.search(legacy, text, re.I)
            assert ((new.span(), new.groups()) if new else None) == ((old.span(), old.groups()) if old else None)


@pytest.mark.parametrize("program", [
    "from routes.chat_helpers import _normalize_thinking as f; assert f('a'+'\\n\\n'*100000+'X') == 'a'+'\\n\\n'*100000+'X'",
    "from src.clean_agent_preview import unbound_lookup_reference as f; assert not f('\\t'*100000+'X', []); assert f('\\t'*100000+'look it up', [])",
    "from src.agent_loop import _is_terse_link_request as f; assert not f('links for'+'\\t'*100000+'X')",
    "from src.tool_parsing import _iter_qwen_python_args as f; assert list(f('a='+'\\t'*100000+',')) == [('a', '\\t')]",
])
def test_unchanged_linear_alert_shapes_complete_under_process_deadline(program):
    subprocess.run([sys.executable, "-c", program], check=True, timeout=8, capture_output=True, text=True)
