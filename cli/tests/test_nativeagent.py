"""Unit tests for the native agent loop's tools and call extraction."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from odysseus_cli import nativeagent as na  # noqa: E402


# ── read-only tool implementations ─────────────────────────────────────────
def test_read_file_ok(tmp_path):
    (tmp_path / "a.py").write_text("print('hi')\n")
    assert "print('hi')" in na._read_file(tmp_path, "a.py")


def test_read_file_outside_root_blocked(tmp_path):
    out = na._read_file(tmp_path, "../escape.txt")
    assert "outside the project root" in out


def test_read_file_missing(tmp_path):
    assert "file not found" in na._read_file(tmp_path, "nope.py")


def test_list_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "readme.md").write_text("x")
    out = na._list_dir(tmp_path, ".")
    assert "src/" in out and "readme.md" in out


def test_grep_finds_match(tmp_path):
    (tmp_path / "code.py").write_text("def foo():\n    return 42\n")
    out = na._grep(tmp_path, "return", ".")
    assert "code.py:2" in out and "return 42" in out


def test_grep_no_match(tmp_path):
    (tmp_path / "code.py").write_text("nothing here\n")
    assert na._grep(tmp_path, "xyzzy", ".") == "(no matches)"


# ── tool-call extraction (native + content fallback) ───────────────────────
def test_calls_from_native_field():
    msg = {"tool_calls": [
        {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
    ]}
    assert na._calls_from_message(msg) == [("read_file", {"path": "a.py"})]


def test_calls_from_content_json_fallback():
    # Ollama returns qwen tool calls as JSON in content, not the native field.
    msg = {"content": '{"name": "read_file", "arguments": {"path": "a.py"}}',
           "tool_calls": None}
    calls = na._calls_from_message(msg)
    assert calls == [("read_file", {"path": "a.py"})]


def test_calls_from_content_arg_alias_normalized():
    msg = {"content": '```json\n{"name": "read_file", "arguments": {"file_path": "x.py"}}\n```'}
    calls = na._calls_from_message(msg)
    assert calls == [("read_file", {"path": "x.py"})]  # file_path -> path


def test_no_calls_when_plain_answer():
    msg = {"content": "The bug is on line 7: division by zero."}
    assert na._calls_from_message(msg) == []


# ── todo_write planning tool ───────────────────────────────────────────────
def test_todo_write_stores_and_summarizes():
    from odysseus_cli.approval import ApprovalState
    state = ApprovalState("deny")
    todos = [
        {"content": "Read the file", "status": "completed"},
        {"content": "Fix the bug", "status": "in_progress"},
        {"content": "Run tests", "status": "pending"},
    ]
    out = na._todo_write(state, todos)
    assert state.todos == todos
    assert "3 task(s)" in out and "1 completed" in out


def test_compact_noop_for_short_history():
    import asyncio
    from odysseus_cli.config import CliConfig
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    out = asyncio.run(na.compact(CliConfig(), msgs))
    assert out == msgs  # nothing to compact → unchanged, no network call


def test_todo_write_skips_malformed_entries():
    from odysseus_cli.approval import ApprovalState
    state = ApprovalState("deny")
    na._todo_write(state, [{"content": "ok", "status": "pending"}, {"status": "pending"}, "bad"])
    assert len(state.todos) == 1
    assert state.todos[0]["content"] == "ok"
