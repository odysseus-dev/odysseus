import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.McpServer = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    path = ROOT / "scripts" / "odysseus-mcp"
    loader = importlib.machinery.SourceFileLoader("odysseus_mcp_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_mcp_json_helpers_reject_wrong_shapes(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._json_list('["a"]') == ["a"]
    assert cli._json_list('{"not":"list"}') == []
    assert cli._json_list("{bad") == []
    assert cli._json_dict('{"A":"B"}') == {"A": "B"}
    assert cli._json_dict('["bad"]') == {}
    assert cli._json_dict("{bad") == {}
