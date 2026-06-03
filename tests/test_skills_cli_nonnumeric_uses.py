import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


def _load_cli(monkeypatch):
    svc = ModuleType("services.memory.skills")
    svc.SkillsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.skills", svc)
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-skills"
    loader = importlib.machinery.SourceFileLoader("odysseus_skills_cli_uses_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_cmd_list_handles_non_numeric_uses(monkeypatch, capsys):
    # skills.json can be hand-edited/corrupt; a non-numeric "uses" made the
    # sort key call int("abc") and raise ValueError, breaking the whole listing.
    cli = _load_cli(monkeypatch)
    mgr = MagicMock()
    mgr.load_all.return_value = [
        {"name": "a", "uses": "abc", "category": "general"},
        {"name": "b", "uses": 3, "category": "general"},
    ]
    monkeypatch.setattr(cli, "_manager", lambda: mgr)
    cli.cmd_list(SimpleNamespace(category=None, limit=50, pretty=False))
    names = {r["name"] for r in json.loads(capsys.readouterr().out)}
    assert names == {"a", "b"}
