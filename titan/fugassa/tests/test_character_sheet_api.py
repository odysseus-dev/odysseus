"""Character sheet compute/validate API smoke tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import dnd5e_options as opt
from titan.fugassa.dnd5e_character_builder import validate_sheet_input
from titan.fugassa.sheet_persistence import build_sheet_from_draft


def _wizard_draft_incomplete():
    wizard_idx = opt.CLASS_CHOICES.index("Wizard")
    elf_idx = opt.RACE_CHOICES.index("Elf")
    return {
        "player_name": "Elara",
        "level": 1,
        "player_class_idx": wizard_idx,
        "player_race_idx": elf_idx,
        "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        "skill_proficiencies": {"arcana": True, "history": True},
        "selected_cantrips": ["fire-bolt"],
        "playstyle": "adventure",
        "rules_mode": "5e-style",
    }


def test_validate_incomplete_wizard_blocks():
    result = validate_sheet_input(_wizard_draft_incomplete())
    assert result["ok"] is False
    assert result["errors"]


def test_validate_freeform_allows_incomplete():
    draft = _wizard_draft_incomplete()
    draft["playstyle"] = "slice_of_life"
    result = validate_sheet_input(draft)
    assert result["ok"] is True


def test_compute_wizard_has_spellcasting():
    draft = _wizard_draft_incomplete()
    draft["selected_cantrips"] = ["fire-bolt", "light", "mage-hand"]
    draft["selected_spells_by_level"] = {"1": ["magic-missile", "shield"]}
    sheet, _ = build_sheet_from_draft(draft)
    assert sheet["spellcasting"]["has"] is True
    assert sheet["spellcasting"]["cantrips_known"] == 3
