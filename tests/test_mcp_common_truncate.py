"""Regression: the shared MCP truncate() must tolerate non-string input."""
import importlib.machinery
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "mcp_servers" / "_common.py"


def _load():
    loader = importlib.machinery.SourceFileLoader("odysseus_mcp_common", str(_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_truncate_handles_none_and_nonstring():
    c = _load()
    assert c.truncate(None) == ""
    assert c.truncate(12345) == "12345"


def test_truncate_string_behaviour_unchanged():
    c = _load()
    assert c.truncate("hello", limit=100) == "hello"
    out = c.truncate("x" * 50, limit=10)
    assert out.startswith("x" * 10) and "truncated" in out
