"""Regression tests for #7120: standalone /tmp/ path rewriting in bash and python tools."""
from __future__ import annotations

import os
import pytest

from src.agent_tools.subprocess_tools import (
    AGENT_ISOLATED_TMP_DIRNAME,
    BashTool,
    PythonTool,
    _rewrite_isolated_tmp_paths,
)
from src.tool_execution import _active_workspace


@pytest.mark.parametrize(
    ("command", "expected_subpath"),
    [
        ("rm /tmp/a.tmp", "a.tmp"),
        ("cat '/tmp/a.tmp'", "a.tmp"),
        ('cat "/tmp/a.tmp"', "a.tmp"),
        ("OUT=/tmp/out.txt echo hi", "out.txt"),
        ("python -c 'open(\"/tmp/data.json\")'", "data.json"),
        ("echo (/tmp/sub/file)", "sub/file"),
        ("items=[/tmp/one,/tmp/two]", "one"),
    ],
)
def test_rewrites_standalone_tmp_paths_and_creates_isolated_dir(tmp_path, command, expected_subpath):
    ws = str(tmp_path)
    rewritten = _rewrite_isolated_tmp_paths(command, ws)
    isolated_dir = os.path.join(ws, AGENT_ISOLATED_TMP_DIRNAME)
    expected_prefix = isolated_dir.rstrip("/") + "/"

    assert "/tmp/" not in rewritten
    assert expected_prefix + expected_subpath in rewritten
    assert os.path.isdir(isolated_dir)


@pytest.mark.parametrize(
    "command",
    [
        "rm ./tmp/a.tmp",
        "ls ~/tmp/a",
        "cat /var/tmp/a",
        "cat $HOME/tmp/a",
        "cat data/tmp/a",
        "rm tmp/a.tmp",
        'open(f"{root}/tmp/a")',
        "path = '/srv/ws/tmp/a.tmp'",
    ],
)
def test_preserves_non_root_tmp_segments_without_creating_dot_tmp(tmp_path, command):
    ws = str(tmp_path)
    rewritten = _rewrite_isolated_tmp_paths(command, ws)
    isolated_dir = os.path.join(ws, AGENT_ISOLATED_TMP_DIRNAME)

    assert rewritten == command
    assert not os.path.exists(isolated_dir)


@pytest.mark.asyncio
async def test_bash_tool_does_not_rewrite_relative_tmp_path(tmp_path, monkeypatch):
    ws_tmp = tmp_path / "tmp"
    ws_tmp.mkdir()
    target = ws_tmp / "a.tmp"
    target.write_text("scratch", encoding="utf-8")

    captured: list[str] = []

    async def fake_run_owned_command(cmd, ctx, **kwargs):
        captured.append(cmd)
        return {"output": "ok", "exit_code": 0}

    monkeypatch.setattr("src.agent_runtime.process_resources.require_launch", lambda *a, **kw: None)
    monkeypatch.setattr("src.agent_tools.subprocess_tools._run_owned_command", fake_run_owned_command)

    token = _active_workspace.set(str(tmp_path))
    try:
        res = await BashTool().execute("rm ./tmp/a.tmp", {})
    finally:
        _active_workspace.reset(token)

    assert res["exit_code"] == 0
    assert captured == ["rm ./tmp/a.tmp"]
    assert not (tmp_path / AGENT_ISOLATED_TMP_DIRNAME).exists()


@pytest.mark.asyncio
async def test_python_tool_rewrites_only_standalone_tmp_paths(tmp_path, monkeypatch):
    captured: list[list[str]] = []

    async def fake_run_owned_command(cmd_argv, ctx, **kwargs):
        captured.append(cmd_argv)
        return {"output": "ok", "exit_code": 0}

    monkeypatch.setattr("src.agent_runtime.process_resources.require_launch", lambda *a, **kw: None)
    monkeypatch.setattr("src.agent_tools.subprocess_tools._run_owned_command", fake_run_owned_command)

    code = 'a = open(f"{root}/tmp/in.txt"); b = open("/tmp/out.txt", "w")'
    token = _active_workspace.set(str(tmp_path))
    try:
        res = await PythonTool().execute(code, {})
    finally:
        _active_workspace.reset(token)

    assert res["exit_code"] == 0
    assert len(captured) == 1
    rendered_script = captured[0][-1]
    isolated_prefix = os.path.join(str(tmp_path), AGENT_ISOLATED_TMP_DIRNAME).rstrip("/") + "/"
    assert 'f"{root}/tmp/in.txt"' in rendered_script
    assert f'open("{isolated_prefix}out.txt", "w")' in rendered_script
