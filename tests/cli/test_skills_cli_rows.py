import sys
import types
from unittest.mock import MagicMock

from tests.helpers.cli_loader import load_script


def _load_cli(monkeypatch):
    svc = types.ModuleType("services.memory.skills")
    svc.SkillsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.skills", svc)
    return load_script("odysseus-skills")


def test_skill_entries_skips_invalid_rows(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._skill_entries([
        {"name": "deploy", "category": "ops"},
        "bad-row",
        None,
    ]) == [{"name": "deploy", "category": "ops"}]


def test_summary_normalizes_invalid_use_count(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._summary({"name": "deploy", "uses": "bad"})["uses"] == 0
    assert cli._summary({"name": "ops", "uses": "3"})["uses"] == 3
