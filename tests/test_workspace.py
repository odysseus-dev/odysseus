from pathlib import Path

import pytest

from src.workspace import WorkspaceError, resolve_workspace_dir, resolve_workspace_path


def test_resolve_workspace_dir_prefers_explicit_cwd(monkeypatch, tmp_path):
    explicit = tmp_path / "explicit"
    setting = tmp_path / "setting"
    env = tmp_path / "env"
    explicit.mkdir()
    setting.mkdir()
    env.mkdir()

    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(setting))
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_DIR", str(env))

    assert resolve_workspace_dir(str(explicit)) == str(explicit.resolve())


def test_resolve_workspace_dir_uses_setting(monkeypatch, tmp_path):
    setting = tmp_path / "setting"
    env = tmp_path / "env"
    setting.mkdir()
    env.mkdir()

    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(setting))
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_DIR", str(env))

    assert resolve_workspace_dir() == str(setting.resolve())


def test_resolve_workspace_dir_uses_env_fallback(monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.mkdir()

    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: "")
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_DIR", str(env))

    assert resolve_workspace_dir() == str(env.resolve())


def test_resolve_workspace_dir_defaults_to_home(monkeypatch):
    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: "")
    monkeypatch.delenv("ODYSSEUS_WORKSPACE_DIR", raising=False)

    assert resolve_workspace_dir() == str(Path.home())


def test_resolve_workspace_dir_rejects_missing(monkeypatch, tmp_path):
    missing = tmp_path / "missing"
    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(missing))

    with pytest.raises(WorkspaceError, match="does not exist"):
        resolve_workspace_dir()


def test_resolve_workspace_dir_rejects_files(monkeypatch, tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(file_path))

    with pytest.raises(WorkspaceError, match="not a directory"):
        resolve_workspace_dir()


def test_resolve_workspace_path_anchors_relative_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(tmp_path))

    expected = tmp_path / "notes" / "todo.txt"
    assert resolve_workspace_path("notes/todo.txt") == str(expected.resolve())


def test_resolve_workspace_path_leaves_absolute_paths(monkeypatch, tmp_path):
    absolute = tmp_path / "todo.txt"
    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(tmp_path / "other"))

    assert resolve_workspace_path(str(absolute)) == str(absolute)


async def test_agent_bash_tool_uses_workspace_setting(monkeypatch, tmp_path):
    from src.tool_execution import _direct_fallback

    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(tmp_path))

    result = await _direct_fallback("bash", "pwd")

    assert result["exit_code"] == 0
    assert result["output"] == str(tmp_path)


async def test_agent_python_tool_uses_workspace_setting(monkeypatch, tmp_path):
    from src.tool_execution import _direct_fallback

    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(tmp_path))

    result = await _direct_fallback("python", "import os; print(os.getcwd())")

    assert result["exit_code"] == 0
    assert result["output"] == str(tmp_path)


async def test_agent_file_tools_anchor_relative_paths(monkeypatch, tmp_path):
    from src.tool_execution import _direct_fallback

    monkeypatch.setattr("src.workspace.get_setting", lambda key, default=None: str(tmp_path))

    written = await _direct_fallback("write_file", "notes/todo.txt\nhello")
    read = await _direct_fallback("read_file", "notes/todo.txt")

    assert written["exit_code"] == 0
    assert (tmp_path / "notes" / "todo.txt").read_text(encoding="utf-8") == "hello"
    assert read == {"output": "hello", "exit_code": 0}
