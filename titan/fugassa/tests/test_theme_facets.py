"""Tests for wizard-time theme facet normalization."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import asset_prompts as ap
from titan.fugassa import game_bootstrap as gb
from titan.fugassa.theme_facet_engine import (
    ensure_theme_facets_in_state,
    resolve_theme_facets,
)
from titan.fugassa.asset_prompts import sanitize_theme_facets


def test_sanitize_theme_facets_filters_unknown():
    assert sanitize_theme_facets(["dark_fantasy", "dystopian", "space_opera", "dark-fantasy"]) == [
        "dark_fantasy",
        "dystopian",
    ]


def test_resolve_theme_facets_prefers_stored_over_czech_theme_string():
    theme = "Temná fantasy v dystopické budoucnosti"
    stored = ["dark_fantasy", "dystopian"]
    facets = resolve_theme_facets(theme, stored=stored)
    assert facets == frozenset({"dark_fantasy", "dystopian"})
    anchor = ap.theme_scene_positive_anchor(theme, facets=facets, theme_label="dark fantasy dystopian")
    assert "dark fantasy" in anchor.lower()
    assert "dystopian" in anchor.lower()
    neg = ap.merge_scene_theme_negative("", theme, facets=facets)
    assert "cyberpunk city" not in neg.lower()


def test_apply_wizard_draft_persists_theme_facets():
    state = gb.build_initial_game_state("test-save", "Custom")
    draft = {
        "player_name": "Hero",
        "level": 1,
        "theme_facets": ["dark_fantasy", "dystopian"],
        "theme_label_en": "dark fantasy dystopian future",
        "world_information": "Kampaň v češtině.",
    }
    state = gb.apply_wizard_draft(state, draft, theme="Temná fantasy")
    wp = state["world_profile"]
    assert wp["theme_facets"] == ["dark_fantasy", "dystopian"]
    assert wp["theme_label_en"] == "dark fantasy dystopian future"


def test_ensure_theme_facets_backfills_legacy_save():
    state = {
        "world_profile": {
            "theme": "dark fantasy / dystopian future",
            "world_information": "",
        }
    }
    changed = ensure_theme_facets_in_state(state)
    assert changed is True
    assert "dark_fantasy" in state["world_profile"]["theme_facets"]
    assert "dystopian" in state["world_profile"]["theme_facets"]


def test_scene_theme_bundle_uses_english_label():
    theme = "Custom"
    wp = {
        "theme_facets": ["dark_fantasy", "sci_fi"],
        "theme_label_en": "dark fantasy science fiction",
    }
    label, facets = ap.scene_theme_bundle(theme, wp)
    assert label == "dark fantasy science fiction"
    assert facets == frozenset({"dark_fantasy", "sci_fi"})
