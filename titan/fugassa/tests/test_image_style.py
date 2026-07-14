"""Campaign image-style selection (Genre tab → world_profile.image_style)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import asset_gen, game_bootstrap as gb


def test_resolve_image_style_campaign_overrides_theme():
    assert asset_gen.resolve_image_style(theme="Sci-fi", campaign_style="anime") == "anime"
    assert asset_gen.resolve_image_style(theme="Fantasy", campaign_style="pixelart") == "pixelart"


def test_resolve_image_style_auto_falls_back_to_theme():
    assert asset_gen.resolve_image_style(theme="Sci-fi", campaign_style="auto") == "krea"
    assert asset_gen.resolve_image_style(theme="Fantasy", campaign_style="") == "realistic"


def test_resolve_image_style_global_default_before_theme_auto():
    assert asset_gen.resolve_image_style(theme="Fantasy", global_default="anime") == "anime"
    assert asset_gen.resolve_image_style(theme="Fantasy", campaign_style="pixelart", global_default="anime") == "pixelart"


def test_apply_wizard_draft_persists_image_style():
    state = gb.build_initial_game_state("Test", "Fantasy")
    draft = {"image_style": "anime", "player_name": "Hero"}
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    assert state["world_profile"]["image_style"] == "anime"


def test_image_style_from_state():
    state = {"world_profile": {"image_style": "krea"}}
    assert asset_gen.image_style_from_state(state) == "krea"
