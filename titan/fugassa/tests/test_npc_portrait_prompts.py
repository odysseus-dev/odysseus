"""Tests for NPC portrait prompt helpers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import npc_portrait_prompts as npp


def test_deterministic_npc_portrait_prompts_includes_name_and_race():
    out = npp.deterministic_npc_portrait_prompts(
        name="Elara Voss",
        race="Elf",
        class_role="concubine",
        backstory_summary="Pureblooded elf with golden hair and pointed ears.",
        theme="dark fantasy",
        style_override="anime",
    )
    pos = out["positive_prompt"]
    assert "Elara Voss" in pos
    assert "single character" in pos
    assert out["negative_prompt"]
