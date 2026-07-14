"""Portrait generation — thin wrapper over asset_gen (ADR §L6)."""

from __future__ import annotations

from typing import Any

from titan.fugassa import asset_gen

generate_portrait = asset_gen.generate_portrait
style_for_theme = asset_gen.style_for_theme

__all__ = ["generate_portrait", "style_for_theme"]
