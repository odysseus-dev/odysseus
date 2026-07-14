"""Inventory Suggest-3 must render player-facing prose, not raw JSON blobs."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import wizard_json as wj

SAMPLE_TWO_OPTIONS = {
    "options": [
        {
            "title": "Option 1",
            "items": [
                {
                    "item_id": "inv_01_waterskin",
                    "name": "Water-skin with Purification Tablets",
                    "description": "A worn leather water-bag.",
                    "quantity": 1,
                    "usage": "exploration",
                }
            ],
            "currency": ["bronze", "silver", "gold"],
        },
        {
            "title": "Option 2",
            "items": [
                {
                    "item_id": "inv_06_silk_cloak",
                    "name": "Silk Cloak, Water-Lord's Gift",
                    "description": "A fine silk cloak.",
                    "quantity": 1,
                    "usage": "utility",
                }
            ],
            "currency": ["credits", "data chips", "reactor cores"],
        },
    ],
    "selection_hint": "Choose one inventory set (1 / 2 / 3)",
}


def test_format_inventory_options_renders_prose_not_json():
    text = wj.format_inventory_options(SAMPLE_TWO_OPTIONS["options"], 1)
    assert "Option 1:" in text
    assert "Water-skin with Purification Tablets" in text
    assert "Option 2:" in text
    assert "Silk Cloak" in text
    assert "Currency: bronze, silver, gold" in text
    assert "{" not in text
    assert "item_id" not in text


def test_try_format_inventory_options_json_parses_llm_blob():
    raw = json.dumps(SAMPLE_TWO_OPTIONS)
    text = wj.try_format_inventory_options_json(raw, 1)
    assert text
    assert "Option 1:" in text
    assert "Choose option 1 or 2" in text
