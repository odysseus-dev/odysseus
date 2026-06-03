import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    svc = types.ModuleType("services.memory.skills")
    svc.SkillsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.skills", svc)
    path = ROOT / "scripts" / "odysseus-skills"
    loader = importlib.machinery.SourceFileLoader("odysseus_skills_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_skill_entries_skips_invalid_rows(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._skill_entries([
        {"name": "deploy", "category": "ops"},
        "bad-row",
        None,
    ]) == [{"name": "deploy", "category": "ops"}]
