"""Reliability contract for the workspace Bash agent tool."""

import os
from pathlib import Path

import pytest

from src.agent_loop import _bash_replays_recent_output
from src.agent_tools import subprocess_tools
from src.constants import MAX_BASH_COMMAND_CHARS
from src.tool_execution import format_tool_result


@pytest.mark.asyncio
async def test_bash_supports_bash_syntax_and_keeps_both_streams(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    result = await subprocess_tools.BashTool().execute(
        'items=(alpha beta); printf "%s\\n" "$PWD" "${items[1]}"; printf "warning\\n" >&2',
        {"subproc_env": dict(os.environ), "session_id": "chat-with-tmux-installed"},
    )

    assert result["exit_code"] == 0
    assert result["stdout"].splitlines() == [str(tmp_path), "beta"]
    assert result["stderr"] == "warning"
    assert "STDERR: warning" in result["output"]


@pytest.mark.asyncio
async def test_bash_calls_are_fresh_and_do_not_leak_directory_state(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    context = {"subproc_env": dict(os.environ), "session_id": "same-chat"}

    first = await subprocess_tools.BashTool().execute(
        "cd /; export ODYSSEUS_LEAK_TEST=yes; pwd", context
    )
    second = await subprocess_tools.BashTool().execute(
        'printf "%s|%s" "$PWD" "${ODYSSEUS_LEAK_TEST-unset}"', context
    )

    assert first["exit_code"] == 0
    assert second["stdout"] == f"{tmp_path}|unset"


def test_bounded_output_keeps_head_and_failure_tail():
    buf = subprocess_tools._BoundedOutput(limit=256)
    buf.append("start\n" + ("x" * 500))
    buf.append("\nFINAL FAILURE")

    output = buf.text()
    assert output.startswith("start\n")
    assert "chars omitted" in output
    assert output.endswith("FINAL FAILURE")


def test_model_receives_command_output_and_exit_code():
    formatted = format_tool_result(
        "bash: npm test",
        {
            "output": "tests passed\nSTDERR: warning",
            "stdout": "tests passed",
            "stderr": "warning",
            "exit_code": 0,
        },
    )

    assert "tests passed" in formatted
    assert "STDERR: warning" in formatted
    assert "**exit_code:** 0" in formatted
    assert formatted.count("tests passed") == 1


def test_gui_opens_bash_output_by_default():
    source = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")

    assert "const _showShellResult = json.tool === 'bash'" in source
    assert "const openOutput = json.tool === 'bash' ? ' open' : ''" in source
    assert "'.agent-thread.streaming .agent-thread-node.running'" in source


def test_multiline_command_output_cannot_be_replayed_as_bash():
    listing = (
        "total 8\n"
        "drwxr-xr-x 2 user user 4.0K Aug 13 12:00 .\n"
        "-rw-r--r-- 1 user user 120 Aug 13 11:00 notes.txt"
    )

    assert _bash_replays_recent_output(listing, [listing]) is True
    assert _bash_replays_recent_output("printf '%s\\n' notes.txt", [listing]) is False


def test_individual_long_listing_rows_cannot_be_replayed_as_bash():
    row = "-rw-r--r-- 1 user user 120 Aug 13 11:00 notes.txt"

    assert _bash_replays_recent_output(row, [f"total 4\n{row}"]) is True


@pytest.mark.asyncio
async def test_large_output_without_newlines_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    result = await subprocess_tools.BashTool().execute(
        "printf 'x%.0s' {1..20000}; printf 'FINAL-TAIL'",
        {"subproc_env": dict(os.environ), "session_id": "large-output"},
    )

    assert result["exit_code"] == 0
    assert "chars omitted" in result["stdout"]
    assert result["stdout"].endswith("FINAL-TAIL")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "message"),
    [
        (" \n\t", "No command provided"),
        ("printf ok\x00ignored", "NUL byte"),
        ("x" * (MAX_BASH_COMMAND_CHARS + 1), "too large"),
    ],
)
async def test_invalid_bash_programs_fail_before_spawn(command, message, monkeypatch):
    async def should_not_spawn(*_args, **_kwargs):
        raise AssertionError("invalid command reached process creation")

    monkeypatch.setattr(subprocess_tools, "_create_bash_subprocess", should_not_spawn)
    result = await subprocess_tools.BashTool().execute(command, {})

    assert result["exit_code"] == 1
    assert message in result["output"]
    assert result["stderr"] == result["error"]
