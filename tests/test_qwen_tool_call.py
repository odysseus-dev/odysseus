import src.agent_tools  # noqa: F401
from src.tool_parsing import (
    parse_tool_blocks,
    strip_tool_blocks,
    _scan_qwen_args,
    _validate_tool_arguments,
)


def test_qwen_tool_call_parsing_and_stripping():
    raw = """Sure, let me check your accounts.

<|tool_call_start|>[list_email_accounts()]<|tool_call_end|>"""

    blocks = parse_tool_blocks(raw, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__list_email_accounts"
    assert blocks[0].content == "{}"
    assert strip_tool_blocks(raw, skip_fenced=True) == "Sure, let me check your accounts."


def test_qwen_tool_call_with_args():
    raw = """Okay, fetching recent messages.
<|tool_call_start|>[list_emails(account="Gmail", unread_only=True, max_results=5)]<|tool_call_end|>"""

    blocks = parse_tool_blocks(raw, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__list_emails"
    assert "Gmail" in blocks[0].content
    
    cleaned = strip_tool_blocks(raw, skip_fenced=True)
    assert cleaned == "Okay, fetching recent messages."


def test_qwen_tool_call_positional_args():
    # Single positional argument
    raw = '<|tool_call_start|>[web_search("Sweden news")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "Sweden news" in blocks[0].content

    # Multiple positional arguments
    raw = '<|tool_call_start|>[read_file("src/main.py", 10, 50)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert "src/main.py" in blocks[0].content
    assert "10" in blocks[0].content
    assert "50" in blocks[0].content


def test_qwen_tool_call_whitespace_before_end_tag():
    raw = "Okay.\n<|tool_call_start|>[web_search(query=\"Sweden news\")]\n<|tool_call_end|>"
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    
    cleaned = strip_tool_blocks(raw, skip_fenced=True)
    assert cleaned == "Okay."


def test_qwen_tool_call_regex_fallback_and_single_arg():
    # Regex fallback with unquoted values containing spaces
    raw = '<|tool_call_start|>[web_search(query=Sweden news today, time_filter=day)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "Sweden news today" in blocks[0].content
    assert "day" in blocks[0].content

    # Single argument fallback (syntax error, no keyword)
    raw = '<|tool_call_start|>[web_search(Sweden news today)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "Sweden news today" in blocks[0].content


def test_qwen_argument_validation():
    # list-valued command in bash
    raw = '<|tool_call_start|>[bash(command=["ls", "-la"])]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0

    # set-valued query in web_search
    raw = '<|tool_call_start|>[web_search(query={"Sweden news"})]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0

    # non-string path/content in write_file
    raw = '<|tool_call_start|>[write_file(path=123, content=["foo"])]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0


def test_qwen_positional_canonicalization():
    # Alias shell maps to bash, and its positional argument maps to command
    raw = '<|tool_call_start|>[shell("pwd")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "pwd"


def test_qwen_email_fail_closed():
    # Positional list_emails call should fail closed (return None/no blocks)
    raw = '<|tool_call_start|>[list_emails("work")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0

    # Keyword list_emails call should parse successfully
    raw = '<|tool_call_start|>[list_emails(account="work")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__list_emails"
    assert "work" in blocks[0].content


def test_qwen_delimiter_parsing():
    # )] sequence inside quotes should be treated as data
    raw = '<|tool_call_start|>[web_search(query="a )] b")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "a )] b" in blocks[0].content

    # Should also strip correctly
    cleaned = strip_tool_blocks(raw, skip_fenced=True)
    assert cleaned == ""


def test_qwen_parser_robustness_incomplete_wrapper():
    # Incomplete wrapper (no closing tag) should not execute or strip
    raw = 'before <|tool_call_start|>[bash("id")] after'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0

    cleaned = strip_tool_blocks(raw, skip_fenced=True)
    assert cleaned == 'before <|tool_call_start|>[bash("id")] after'


def test_qwen_email_malformed_keyword_fail_closed():
    # If key-value evaluation fails for email tools, it should fail closed (return no blocks)
    raw = '<|tool_call_start|>[list_emails(account=work)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0


# ---------------------------------------------------------------------------
# _scan_qwen_args unit tests
# ---------------------------------------------------------------------------

class TestScanQwenArgs:
    def test_simple_args(self):
        result = _scan_qwen_args('query="hello")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert args_str == 'query="hello"'
        assert end_pos == 15  # after ')]'

    def test_empty_args(self):
        result = _scan_qwen_args(")]", 0)
        assert result is not None
        args_str, end_pos = result
        assert args_str == ""
        assert end_pos == 2

    def test_nested_parens(self):
        result = _scan_qwen_args('cmd=subprocess.run(["ls"]) )]', 0)
        assert result is not None
        args_str, end_pos = result
        assert 'cmd=subprocess.run(["ls"])' in args_str

    def test_double_quoted_string_with_paren(self):
        result = _scan_qwen_args(r'query="hello (world)")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert r'query="hello (world)"' in args_str

    def test_single_quoted_string(self):
        result = _scan_qwen_args("query='hello')]", 0)
        assert result is not None
        args_str, end_pos = result
        assert "query='hello'" in args_str

    def test_triple_quoted_string(self):
        result = _scan_qwen_args('command="""ls -la""")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert 'command="""ls -la"""' in args_str

    def test_triple_quoted_at_end_of_input(self):
        # Regression: off-by-one when triple-quote is at the very end
        result = _scan_qwen_args('command="""done""")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert args_str == 'command="""done"""'

    def test_triple_quoted_with_escape(self):
        result = _scan_qwen_args(r'command="""hello\"world""")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert r'command="""hello\"world"""' in args_str

    def test_triple_quoted_escape_at_end(self):
        # Backslash at end of triple-quoted string should not crash
        result = _scan_qwen_args(r'command="""test\")]', 0)
        # Malformed: escape at end of input, should return None
        assert result is None

    def test_unbalanced_paren_returns_none(self):
        result = _scan_qwen_args('query="hello"', 0)
        assert result is None

    def test_unbalanced_bracket_returns_none(self):
        result = _scan_qwen_args('query="["]', 0)
        assert result is None

    def test_bracket_inside_string(self):
        result = _scan_qwen_args('query="["]', 0)
        # The string contains [, but it's quoted, so bracket_nesting stays 0
        # However the string is never closed, so this should return None
        assert result is None

    def test_closing_bracket_without_paren(self):
        # ] without matching ) should not produce a match
        result = _scan_qwen_args('query="hello"]', 0)
        assert result is None

    def test_multiple_kwargs(self):
        result = _scan_qwen_args('query="test", time_filter="day")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert 'query="test"' in args_str
        assert 'time_filter="day"' in args_str

    def test_mixed_quote_styles(self):
        result = _scan_qwen_args('query="test", cmd="echo hi")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert 'query="test"' in args_str
        assert 'cmd="echo hi"' in args_str

    def test_backslash_escape_in_double_quote(self):
        result = _scan_qwen_args(r'query="hello\"world")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert r'query="hello\"world"' in args_str

    def test_backslash_escape_in_single_quote(self):
        result = _scan_qwen_args("query='hello\\'world')]", 0)
        assert result is not None
        args_str, end_pos = result
        assert "query='hello\\'world'" in args_str

    def test_nested_parens_in_string(self):
        result = _scan_qwen_args(r'cmd="echo (1+2)")]', 0)
        assert result is not None
        args_str, end_pos = result
        assert r'cmd="echo (1+2)"' in args_str


# ---------------------------------------------------------------------------
# _validate_tool_arguments unit tests
# ---------------------------------------------------------------------------

class TestValidateToolArguments:
    def test_valid_bash_args(self):
        assert _validate_tool_arguments("bash", {"command": "ls -la"}) is True

    def test_valid_web_search_args(self):
        assert _validate_tool_arguments("web_search", {"query": "test"}) is True

    def test_valid_web_search_with_time_filter(self):
        assert _validate_tool_arguments("web_search", {"query": "test", "time_filter": "day"}) is True

    def test_invalid_bash_command_type(self):
        # bash command must be a string
        assert _validate_tool_arguments("bash", {"command": 123}) is False

    def test_invalid_web_search_query_type(self):
        # web_search query must be a string
        assert _validate_tool_arguments("web_search", {"query": 123}) is False

    def test_missing_required_field(self):
        # web_search requires "query"
        assert _validate_tool_arguments("web_search", {}) is False

    def test_unknown_tool_passes(self):
        # Unknown/MCP tools should pass validation
        assert _validate_tool_arguments("mcp__custom__tool", {"anything": "goes"}) is True

    def test_rejects_non_dict_args(self):
        assert _validate_tool_arguments("bash", "not a dict") is False

    def test_rejects_non_json_serializable(self):
        assert _validate_tool_arguments("bash", {"command": set([1, 2])}) is False

    def test_valid_write_file_args(self):
        assert _validate_tool_arguments("write_file", {"path": "/tmp/test", "content": "hello"}) is True

    def test_invalid_write_file_path_type(self):
        assert _validate_tool_arguments("write_file", {"path": 123, "content": "hello"}) is False

    def test_valid_read_file_with_optional(self):
        assert _validate_tool_arguments("read_file", {"path": "/tmp/test", "offset": 10, "limit": 50}) is True

    def test_boolean_where_string_expected(self):
        assert _validate_tool_arguments("bash", {"command": True}) is False

    def test_integer_where_string_expected(self):
        assert _validate_tool_arguments("web_search", {"query": 42}) is False


# ---------------------------------------------------------------------------
# Regex fallback edge cases
# ---------------------------------------------------------------------------

def test_qwen_bash_equals_in_command():
    # '=' inside a bash command should NOT be parsed as a keyword arg
    raw = '<|tool_call_start|>[bash(echo hello world=foo)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "echo hello world=foo"


def test_qwen_bash_equals_in_quoted_command():
    # '=' inside a quoted bash command should be preserved
    raw = '<|tool_call_start|>[bash(command="echo x=y")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert "echo x=y" in blocks[0].content


def test_qwen_python_equals_in_code():
    # '=' inside python code should NOT be parsed as a keyword arg
    raw = '<|tool_call_start|>[python(code="x = 1 + 2")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"
    assert "x = 1 + 2" in blocks[0].content


def test_qwen_web_search_equals_in_unquoted_value():
    # Unquoted '=' in web_search should still work via regex fallback
    raw = '<|tool_call_start|>[web_search(query=test query, time_filter=week)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "test query" in blocks[0].content
    assert "week" in blocks[0].content


# ---------------------------------------------------------------------------
# Positional args edge cases
# ---------------------------------------------------------------------------

def test_qwen_too_many_positional_args():
    # bash only takes 1 positional arg; 2 should be rejected
    raw = '<|tool_call_start|>[bash("ls", "-la")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0


def test_qwen_read_file_too_many_positional_args():
    # read_file takes 3 positional args; 4 should be rejected
    raw = '<|tool_call_start|>[read_file("src/main.py", 10, 50, 100)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0


def test_qwen_web_search_too_many_positional_args():
    # web_search takes 2 positional args; 3 should be rejected
    raw = '<|tool_call_start|>[web_search("query", "day", "extra")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 0


def test_qwen_bash_single_positional():
    # bash with exactly 1 positional arg should work
    raw = '<|tool_call_start|>[bash("pwd")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "pwd"


def test_qwen_read_file_exact_positional_count():
    # read_file with exactly 3 positional args should work
    raw = '<|tool_call_start|>[read_file("src/main.py", 10, 50)]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert "src/main.py" in blocks[0].content


def test_qwen_web_search_exact_positional_count():
    # web_search with exactly 2 positional args should work
    raw = '<|tool_call_start|>[web_search("query", "day")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"


def test_qwen_unknown_tool_positional_args():
    # Unknown tool with positional args should be rejected (no pos_names)
    raw = '<|tool_call_start|>[custom_tool("arg1", "arg2")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    # Unknown tool: pos_names is None, so positional args are not mapped
    # The call will fail validation or produce empty params
    assert len(blocks) == 0


# ---------------------------------------------------------------------------
# Triple-quote edge cases
# ---------------------------------------------------------------------------

def test_qwen_triple_quoted_bash_command():
    raw = '<|tool_call_start|>[bash(command="""ls -la /tmp""")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert "ls -la /tmp" in blocks[0].content


def test_qwen_triple_quoted_with_newlines():
    raw = '<|tool_call_start|>[python(code="""\nimport os\nprint(os.getcwd())\n""")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"
    assert "import os" in blocks[0].content


def test_qwen_triple_quoted_with_escapes():
    raw = '<|tool_call_start|>[bash(command="""echo \\"hello\\"""")]<|tool_call_end|>'
    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert 'echo "hello"' in blocks[0].content


# ---------------------------------------------------------------------------
# Multiple tool calls in one response
# ---------------------------------------------------------------------------

def test_qwen_multiple_tool_calls():
    raw = """Let me search and then read the file.

<|tool_call_start|>[web_search(query="python tips")]<|tool_call_end|>

<|tool_call_start|>[read_file(path="README.md")]<|tool_call_end|>"""

    blocks = parse_tool_blocks(raw, skip_fenced=True)
    assert len(blocks) == 2
    assert blocks[0].tool_type == "web_search"
    assert blocks[1].tool_type == "read_file"


def test_qwen_strip_multiple_tool_calls():
    raw = """Let me search and then read the file.

<|tool_call_start|>[web_search(query="python tips")]<|tool_call_end|>

<|tool_call_start|>[read_file(path="README.md")]<|tool_call_end|>"""

    cleaned = strip_tool_blocks(raw, skip_fenced=True)
    assert cleaned == "Let me search and then read the file."
