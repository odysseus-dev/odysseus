import os
from src import builtin_mcp


def test_find_npx_prefers_npx_cmd_on_windows(monkeypatch):
    monkeypatch.setattr(builtin_mcp, "IS_WINDOWS", True)

    def fake_which_tool(name):
        if name == "npx.cmd":
            return r"C:\Program Files\nodejs\npx.cmd"
        if name == "npx":
            return r"C:\Program Files\nodejs\npx"
        return None

    monkeypatch.setattr(builtin_mcp, "which_tool", fake_which_tool)
    assert builtin_mcp._find_npx() == r"C:\Program Files\nodejs\npx.cmd"


def test_find_npx_windows_falls_back_to_common_paths_over_extensionless_npx(monkeypatch, tmp_path):
    monkeypatch.setattr(builtin_mcp, "IS_WINDOWS", True)
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    npx_cmd = npm_dir / "npx.cmd"
    npx_cmd.write_text("@echo off\r\n", encoding="utf-8")

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        builtin_mcp,
        "which_tool",
        lambda name: r"C:\Program Files\nodejs\npx" if name == "npx" else None,
    )

    assert builtin_mcp._find_npx() == os.path.join(str(tmp_path), "npm", "npx.cmd")


def test_find_npx_posix_keeps_which_tool_npx(monkeypatch):
    monkeypatch.setattr(builtin_mcp, "IS_WINDOWS", False)
    monkeypatch.setattr(
        builtin_mcp,
        "which_tool",
        lambda name: "/usr/local/bin/npx" if name == "npx" else None,
    )

    assert builtin_mcp._find_npx() == "/usr/local/bin/npx"
