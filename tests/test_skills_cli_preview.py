"""Regression: the skills CLI summary must tolerate a non-string description.

`_summary` did `(skill.get("description") or "")[:200]`. A non-string
description (e.g. a number from a hand-edited/legacy skill store) is truthy, so
`123[:200]` raised TypeError. `_preview_text` coerces non-strings to "".
"""
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    mod = types.ModuleType("services.memory.skills")
    mod.SkillsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.skills", mod)
    path = ROOT / "scripts" / "odysseus-skills"
    loader = importlib.machinery.SourceFileLoader("odysseus_skills_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_preview_text_ignores_non_string(monkeypatch):
    cli = _load_cli(monkeypatch)
    assert cli._preview_text(None) == ""
    assert cli._preview_text(123) == ""
    assert cli._preview_text({"x": 1}) == ""
    assert cli._preview_text("y" * 250) == "y" * 200


def test_summary_does_not_crash_on_non_string_description(monkeypatch):
    cli = _load_cli(monkeypatch)
    out = cli._summary({"name": "n", "description": 123})
    assert out["description"] == ""
