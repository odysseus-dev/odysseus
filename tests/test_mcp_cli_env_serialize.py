"""Regression: mcp CLI _serialize must not crash when env JSON is not an object.

`env_obj = json.loads(s.env)` can yield a list (e.g. env stored as "[1,2]").
`if redact_env and env_obj:` then called `env_obj.items()` -> AttributeError.
Guard with isinstance(dict).
"""
import importlib.machinery
import importlib.util
import sys
import types
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _load(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.McpServer = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    loader = importlib.machinery.SourceFileLoader("odysseus_mcp_cli", str(ROOT / "scripts" / "odysseus-mcp"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def _srv(env):
    return SimpleNamespace(id="s1", name="n", transport="stdio", command="c", args="[]",
                           env=env, url=None, is_enabled=1, oauth_config=None, created_at=None)


def test_serialize_handles_list_env(monkeypatch):
    cli = _load(monkeypatch)
    out = cli._serialize(_srv("[1, 2]"))  # JSON array, not object
    assert out["id"] == "s1"


def test_serialize_redacts_dict_env(monkeypatch):
    cli = _load(monkeypatch)
    out = cli._serialize(_srv('{"API_KEY": "secret"}'))
    assert out["env"] == {"API_KEY": "***"}
