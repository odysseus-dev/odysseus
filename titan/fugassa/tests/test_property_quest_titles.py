"""Tests for strict starting property, quest templates, titles."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import quest_templates as qt
from titan.fugassa.player_property_engine import propose_starting_property
from titan.fugassa.title_engine import default_bonuses_for_tier


def test_most_backgrounds_get_no_property():
    assert propose_starting_property(background="Wanderer") is None
    assert propose_starting_property(background="Soldier") is None


def test_merchant_gets_modest_townhouse():
    prop = propose_starting_property(background="Merchant trader")
    assert prop and prop.get("granted")
    assert prop["property_kind"] == "townhouse"
    assert prop["specs"]["prestige"] == 1
    assert prop["specs"]["modest"] is True


def test_noble_gets_larger_but_still_modest():
    prop = propose_starting_property(background="Noble heir")
    assert prop and prop.get("granted")
    assert prop["property_kind"] in ("townhouse", "estate")
    assert prop["specs"]["prestige"] <= 2


def test_sovereign_gets_no_personal_property():
    prop = propose_starting_property(background="King of Aldoria")
    assert prop and not prop.get("granted")
    assert prop.get("reason") == "sovereign"


def test_minor_quest_requires_rewards():
    err = qt.validate_archivist_quest_op(
        {"title": "Fetch herbs", "description": "Get herbs", "scale": "minor", "rewards_deferred": True}
    )
    assert err


def test_minor_quest_accepts_gold_reward():
    err = qt.validate_archivist_quest_op(
        {
            "title": "Fetch herbs",
            "description": "Get herbs",
            "scale": "minor",
            "rewards": {"gold": 10, "xp": 20},
        }
    )
    assert err is None
    preview = qt.rewards_preview({"gold": 10, "xp": 20})
    assert "10 gold" in preview


def test_major_quest_accepts_deferred_reward():
    err = qt.validate_archivist_quest_op(
        {
            "title": "The Crown's Shadow",
            "description": "Epic arc",
            "scale": "major",
            "rewards_deferred": True,
            "chain_code": "crown_shadow",
        }
    )
    assert err is None
    assert qt.rewards_preview(None, deferred=True) == "Reward to be determined upon completion"


def test_tier4_title_has_bonuses():
    bonuses = default_bonuses_for_tier(4)
    assert bonuses.get("social_bonus", 0) >= 2
    assert bonuses.get("persuasion_bonus", 0) >= 1
