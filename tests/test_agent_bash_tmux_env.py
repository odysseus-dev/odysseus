import asyncio


def test_workspace_alias_rewrite_does_not_duplicate_absolute_host_path():
    from src.agent_tools.subprocess_tools import _replace_workspace_alias

    cwd = "/runs/task/workspace"
    command = "cd /workspace && python /runs/task/workspace/plot.py"
    assert _replace_workspace_alias(command, cwd) == (
        "cd /runs/task/workspace && python /runs/task/workspace/plot.py"
    )


def test_workspace_namespace_preserves_literal_paths_inside_scripts(tmp_path):
    from pathlib import Path
    import subprocess

    from src.agent_tools.subprocess_tools import _wrap_workspace_namespace

    command = _wrap_workspace_namespace(
        "python -c 'from pathlib import Path; Path(\"/workspace/result.txt\").write_text(\"ok\")'",
        str(tmp_path),
    )
    if command is None:
        return
    subprocess.run(command, shell=True, check=True)
    assert (Path(tmp_path) / "result.txt").read_text() == "ok"


def test_direct_bash_subprocess_has_closed_stdin(monkeypatch, tmp_path):
    from src.agent_tools import subprocess_tools
    from src import tool_execution

    from tests.containment_helpers import capture_owned_spawn
    from tests.process_resource_helpers import authorized_handler
    captured = capture_owned_spawn(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(tmp_path))

    result = asyncio.run(authorized_handler(subprocess_tools.BashTool().execute, tmp_path)("echo ok", {}))

    assert result["exit_code"] == 0
    if "ody-boundary" in captured["argv"]:
        assert captured["kwargs"]["stdin"] is asyncio.subprocess.PIPE
        assert captured["stdin_closed"] is True
    else:
        assert captured["kwargs"]["stdin"] is asyncio.subprocess.DEVNULL
    assert not (tmp_path / ".tmp").exists()


def test_agent_bash_timeout_is_bounded_for_interactive_runs():
    from src.agent_tools import subprocess_tools

    assert subprocess_tools.DEFAULT_BASH_TIMEOUT <= 120


def test_bash_rejects_empty_command_instead_of_reporting_success(monkeypatch):
    from src.agent_tools import subprocess_tools

    async def fail_spawn(*_args, **_kwargs):
        raise AssertionError("an empty command must never start a subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_spawn)

    result = asyncio.run(subprocess_tools.BashTool().execute({}, {}))

    assert result["exit_code"] == 1
    assert "command is required" in result["error"]


def test_bash_rejects_unicode_ffmpeg_drawtext_without_explicit_font(monkeypatch, tmp_path):
    from src.agent_tools import subprocess_tools

    async def fail_spawn(*_args, **_kwargs):
        raise AssertionError("an unsafe drawtext command must never start a subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_spawn)
    monkeypatch.setattr(
        subprocess_tools,
        "_resolve_fontfile_for_text",
        lambda _text: "/home/user/.local/share/fonts/NotoSansCJK-Regular.ttc",
    )

    from tests.process_resource_helpers import authorized_handler
    result = asyncio.run(authorized_handler(subprocess_tools.BashTool().execute, tmp_path)(
        "ffmpeg -i in.mp4 -vf \"drawtext=text='你好':x=10:y=10\" out.mp4",
        {},
    ))

    assert result["exit_code"] == 1
    assert "fontfile" in result["error"]
    assert "fc-match" in result["error"]
    assert "/home/user/.local/share/fonts/NotoSansCJK-Regular.ttc" in result["error"]


def test_bash_allows_unicode_ffmpeg_drawtext_with_explicit_fontfile(monkeypatch, tmp_path):
    from src.agent_tools import subprocess_tools
    from src import tool_execution

    from tests.containment_helpers import capture_owned_spawn
    captured = capture_owned_spawn(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(tmp_path))

    command = (
        "ffmpeg -i in.mp4 -vf \"drawtext=fontfile=/fonts/NotoSansCJK.ttc:"
        "text='你好':x=10:y=10\" out.mp4"
    )
    from tests.process_resource_helpers import authorized_handler
    result = asyncio.run(authorized_handler(subprocess_tools.BashTool().execute, tmp_path)(command, {}))

    assert result["exit_code"] == 0
    assert "drawtext" in captured["command"]
