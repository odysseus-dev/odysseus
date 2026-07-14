"""Wizard draft persistence — selection dicts must replace, not merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from titan.fugassa import wizard_draft_store


@pytest.fixture()
def draft_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "fugassa"
    root.mkdir()
    path = root / "wizard_draft.json"
    monkeypatch.setattr(wizard_draft_store, "FUGASSA_ROOT", str(root))
    monkeypatch.setattr(wizard_draft_store, "WIZARD_DRAFT_PATH", str(path))
    return path


def test_save_skill_proficiencies_replaces_instead_of_merge(draft_path: Path) -> None:
    draft_path.write_text(
        json.dumps({"skill_proficiencies": {"acrobatics": True, "athletics": True, "insight": True}}),
        encoding="utf-8",
    )
    saved = wizard_draft_store.save({"skill_proficiencies": {"perception": True}})
    assert saved["skill_proficiencies"] == {"perception": True}


def test_save_expertise_replaces_instead_of_merge(draft_path: Path) -> None:
    draft_path.write_text(
        json.dumps({"expertise": {"stealth": True, "perception": True}}),
        encoding="utf-8",
    )
    saved = wizard_draft_store.save({"expertise": {"stealth": True}})
    assert saved["expertise"] == {"stealth": True}


def test_save_selected_cantrips_replaces_instead_of_merge(draft_path: Path) -> None:
    draft_path.write_text(
        json.dumps({"selected_cantrips": ["fire-bolt", "light", "mage-hand"]}),
        encoding="utf-8",
    )
    saved = wizard_draft_store.save({"selected_cantrips": ["hbspell:srdfallback:fire-bolt"]})
    assert saved["selected_cantrips"] == ["hbspell:srdfallback:fire-bolt"]


def test_save_homebrew_choices_replaces_instead_of_merge(draft_path: Path) -> None:
    draft_path.write_text(
        json.dumps({"homebrew_choices": {"trait:versatile": "stealth", "feature:tool-proficiency": "old"}}),
        encoding="utf-8",
    )
    saved = wizard_draft_store.save({"homebrew_choices": {"feature:tool-proficiency": "tinker's tools"}})
    assert saved["homebrew_choices"] == {"feature:tool-proficiency": "tinker's tools"}


def test_save_abilities_still_merge(draft_path: Path) -> None:
    draft_path.write_text(json.dumps({"abilities": {"str": 10, "dex": 14}}), encoding="utf-8")
    saved = wizard_draft_store.save({"abilities": {"str": 15}})
    assert saved["abilities"]["str"] == 15
    assert saved["abilities"]["dex"] == 14
