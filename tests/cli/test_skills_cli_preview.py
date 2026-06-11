"""Regression: the skills CLI summary must tolerate a non-string description.

`_summary` did `(skill.get("description") or "")[:200]`. A non-string
description (e.g. a number from a hand-edited/legacy skill store) is truthy, so
`123[:200]` raised TypeError. `_preview_text` coerces non-strings to "".
"""
import sys
import types
from unittest.mock import MagicMock

from tests.helpers.cli_loader import load_script


def _load_cli(monkeypatch):
    mod = types.ModuleType("services.memory.skills")
    mod.SkillsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.skills", mod)
    return load_script("odysseus-skills")


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
