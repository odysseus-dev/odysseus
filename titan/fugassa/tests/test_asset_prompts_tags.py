"""Tests for asset_prompts tag normalization."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import asset_prompts as ap


def test_prose_to_tags_splits_sentences():
    out = ap.prose_to_tags("A damp stone hall. Moss covers the walls and cold air drifts.")
    assert "," in out
    assert "damp stone hall" in out
    assert "Moss covers the walls" in out


def test_build_scene_prompt_is_tag_like():
    out = ap.build_scene_prompt(
        location_name="Whispering Crypt",
        description="Stone arches drip with moss. Torchlight flickers.",
        biome="underground tomb",
        theme="dark fantasy",
        time_of_day="night",
        weather="fog",
    )
    assert "dark fantasy" in out
    assert "Whispering Crypt" in out
    assert out.count(",") >= 6


def test_build_scene_refinement_prompt_strips_composition():
    comp = (
        "medium wide shot, hero in foreground as focal subject, environmental RPG scene, "
        "Elara, silver hair, arched windows, daylight, tavern interior"
    )
    refine = ap.build_scene_refinement_prompt(comp, style="anime", theme="dark fantasy")
    assert "medium wide shot" not in refine.lower()
    assert "hero in foreground" not in refine.lower()
    assert "Elara" in refine
    assert "arched windows" in refine
    assert "highly detailed" in refine
    assert "dark fantasy" in refine.lower()


def test_apply_theme_to_scene_prompt_front_loads_genre():
    out = ap.apply_theme_to_scene_prompt("Elara in tavern", theme="dark fantasy")
    assert "dark fantasy" in out.lower()
    assert "gothic" in out.lower()


def test_merge_scene_theme_negative_blocks_modern_leaks():
    neg = ap.merge_scene_theme_negative("blurry", "fantasy")
    assert "tie" in neg.lower()
    assert "suit" in neg.lower()
    assert "cyberpunk city" in neg.lower()


def test_hybrid_dark_fantasy_dystopian_keeps_both_facets():
    theme = "dark fantasy / dystopian future"
    facets = ap.detect_theme_facets(theme)
    assert "dark_fantasy" in facets
    assert "dystopian" in facets
    anchor = ap.theme_scene_positive_anchor(theme)
    assert "dark fantasy" in anchor.lower()
    assert "dystopian" in anchor.lower()
    neg = ap.merge_scene_theme_negative("", theme)
    assert "cyberpunk city" not in neg.lower()
    assert "tie" in neg.lower()
