"""Regression: rag_server add/remove_directory must not crash on a non-string path.

`directory = arguments.get("directory", "").strip()` runs before the surrounding
try, so a non-string `directory` in the tool args (e.g. a number) raised
AttributeError out of call_tool. Coerce non-strings to "".
"""
import asyncio

import pytest

pytest.importorskip("mcp")

import mcp_servers.rag_server as rs


def _call(monkeypatch, action, directory):
    monkeypatch.setattr(rs, "_ensure_init", lambda: None)
    return asyncio.run(rs.call_tool("manage_rag", {"action": action, "directory": directory}))


def test_add_directory_non_string_does_not_crash(monkeypatch):
    out = _call(monkeypatch, "add_directory", 123)
    assert "needs a directory path" in out[0].text


def test_remove_directory_non_string_does_not_crash(monkeypatch):
    out = _call(monkeypatch, "remove_directory", ["x"])
    assert "needs a directory path" in out[0].text
