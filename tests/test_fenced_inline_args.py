"""PR #3681 — fenced tool calls with inline args, and the fence-tag boundary.

Local fenced-block models (Ollama etc.) emit calls like ```list_email_accounts {}
with the args on the same line as the tag; the parser must execute those. The
relaxed tag pattern must NOT prefix-match longer fence tags: ```python3 is a
language hint, not a "python" tool call with content "3\n...".
"""
import sys
from unittest.mock import MagicMock

for mod in ['src.agent_tools', 'src.tool_parsing', 'src.tool_schemas', 'src.tool_execution']:
    sys.modules.pop(mod, None)
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'core.models', 'core.database', 'core.auth'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import src.agent_tools  # noqa: E402, F401
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks  # noqa: E402


def test_inline_args_on_tag_line_parse():
    # The original bug: ```list_email_accounts {}  (args on the tag line)
    # never matched because the regex required a newline right after the tag.
    blocks = parse_tool_blocks('```list_email_accounts {}\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [("list_email_accounts", "{}")]


def test_inline_json_args_parse_for_email_tools():
    blocks = parse_tool_blocks('```list_emails {"max_results": 5}\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [("list_emails", '{"max_results": 5}')]


def test_next_line_content_still_parses():
    # No regression for the classic shape: tag, newline, content.
    blocks = parse_tool_blocks('```manage_memory\nadd\nsome text\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [("manage_memory", "add\nsome text")]


def test_plain_bash_fence_still_parses():
    blocks = parse_tool_blocks('```bash\necho hello\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "echo hello")]


def test_python3_language_hint_is_not_a_python_tool_call():
    # ```python3 must not prefix-match the "python" fence tag — without the
    # (?![\w-]) boundary it parsed as tool "python" with content "3\nprint(...)"
    # and executed as code.
    blocks = parse_tool_blocks('```python3\nprint("hi")\n```')
    assert blocks == [], blocks


def test_hyphenated_tag_is_not_a_tool_call():
    blocks = parse_tool_blocks('```bash-session\n$ ls\n```')
    assert blocks == [], blocks


def test_markdown_info_string_is_not_executable_python():
    # ```python title="example.py" is Markdown fence metadata, not tool args.
    # Same-line content other than JSON args ({...}/[...]) must not execute —
    # otherwise a fence the model meant to display runs as code.
    blocks = parse_tool_blocks('```python title="example.py"\nprint("hi")\n```')
    assert blocks == [], blocks


def test_markdown_info_string_is_not_executable_bash():
    blocks = parse_tool_blocks('```bash title="setup"\necho hi\n```')
    assert blocks == [], blocks


def test_empty_email_fence_is_an_executable_call():
    # ```list_email_accounts``` with no body is a real shape local models emit
    # for no-arg tools — it must dispatch (with empty args), not vanish.
    blocks = parse_tool_blocks('```list_email_accounts\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [("list_email_accounts", "")]


def test_empty_non_email_fence_still_skipped():
    # Empty bash/python/other fences stay inert: empty content is nothing to run.
    for tag in ("bash", "python", "manage_memory"):
        assert parse_tool_blocks(f'```{tag}\n```') == []


def test_empty_email_fence_is_stripped_from_display():
    # Executed (empty-args) email fences mirror like any executed fence.
    text = 'One sec.\n```list_email_accounts\n```\nDone.'
    assert strip_tool_blocks(text) == 'One sec.\n\nDone.'


def test_inline_json_array_args_still_parse():
    # The narrowed same-line rule must keep accepting JSON args: { or [.
    blocks = parse_tool_blocks('```bulk_email {"action": "archive", "uids": [1, 2]}\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [
        ("bulk_email", '{"action": "archive", "uids": [1, 2]}')
    ]


def test_brace_metadata_on_bash_is_not_executable():
    # ```bash {title="setup"} is a Markdown fence attribute on a real
    # language. Code tags (bash/python) never take same-line args — even a
    # brace-shaped info string must stay display text.
    blocks = parse_tool_blocks('```bash {title="setup"}\necho hi\n```')
    assert blocks == [], blocks


def test_valid_json_metadata_on_python_is_not_executable():
    # Same rule when the attribute happens to BE valid JSON: the tag decides.
    blocks = parse_tool_blocks('```python {"title": "example.py"}\nprint("hi")\n```')
    assert blocks == [], blocks


def test_invalid_inline_json_on_email_tool_is_not_executable():
    # JSON-args tools only execute same-line content that parses as JSON —
    # {title="x"} is metadata/garbage, not arguments.
    blocks = parse_tool_blocks('```list_emails {title="x"}\n```')
    assert blocks == [], blocks


def test_inline_json_continuing_on_next_lines_still_parses():
    # A JSON object opened on the tag line may close on a later line.
    blocks = parse_tool_blocks('```list_emails {"folder": "INBOX",\n"max_results": 5}\n```')
    assert [(b.tool_type, b.content) for b in blocks] == [
        ("list_emails", '{"folder": "INBOX",\n"max_results": 5}')
    ]


def test_brace_metadata_fences_left_intact_in_display():
    # strip must mirror parse for every rejected fence shape.
    for text in (
        'Example:\n```bash {title="setup"}\necho hi\n```',
        'Example:\n```python {"title": "example.py"}\nprint("hi")\n```',
        'Example:\n```list_emails {title="x"}\n```',
    ):
        assert strip_tool_blocks(text) == text


def test_inline_args_fence_is_stripped_from_display():
    # strip must mirror parse: an executed inline-args fence must not leak
    # into the displayed text.
    text = 'Checking now.\n```list_email_accounts {}\n```\nDone.'
    assert strip_tool_blocks(text) == 'Checking now.\n\nDone.'


def test_python3_fence_is_left_intact_in_display():
    # ...and a fence that did NOT parse as a tool call must stay visible.
    text = 'Example:\n```python3\nprint("hi")\n```'
    assert strip_tool_blocks(text) == text


def test_markdown_info_string_fence_is_left_intact_in_display():
    # strip must mirror parse for info-string fences too: not executed,
    # so not stripped from the displayed text.
    text = 'Example:\n```python title="example.py"\nprint("hi")\n```'
    assert strip_tool_blocks(text) == text


def test_parse_strip_mirror_across_fence_shape_grid():
    # Invariant for ANY single fence: either it executes AND is stripped, or
    # it doesn't execute AND stays fully visible. The one allowed exception is
    # an empty NON-EMAIL tool fence (no header, no body): never executed, but
    # stripped as noise — pre-PR behavior, kept deliberately. (Empty EMAIL
    # fences execute with empty args, so they fall under the first branch.)
    from src.agent_tools import TOOL_TAGS

    tags = ["bash", "python", "list_emails", "bulk_email", "manage_memory",
            "python3", "bash-session", "notatool"]
    headers = ["", " ", ' title="x"', ' {title="x"}', ' {"a": 1}', " [1, 2]",
               " {bad json", ' {"a": 1} extra']
    bodies = ["", "content line\n", '{"k": "v"}\n']

    for tag in tags:
        for header in headers:
            for body in bodies:
                text = f"before\n```{tag}{header}\n{body}```\nafter"
                blocks = parse_tool_blocks(text)
                stripped = strip_tool_blocks(text)
                case = (tag, header, body)
                if blocks:
                    assert stripped == "before\n\nafter", case
                elif stripped != text:
                    assert (
                        tag in TOOL_TAGS and not header.strip() and not body.strip()
                    ), f"non-executed fence was stripped: {case}"
