"""An older / partial presets.json must be healed forward on load: built-in
presets that are missing get filled in, WITHOUT clobbering user edits.

This extends the adjacent legacy `custom`-shape migration in
`PresetManager.load`, which already repairs forward-incompatible files and
re-saves them. A missing built-in is never an intentional user action — there
is no delete path for the built-in keys (only `user_templates` entries can be
deleted), and presets are hidden via an `enabled: False` flag, not removal — so
filling them back in is safe.
"""
import json
import os
import tempfile

from src.preset_manager import PresetManager


def _write_presets(data: dict) -> str:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "presets.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return d


def test_missing_builtin_presets_are_filled_in():
    # Partial file: has code_analyze + brainstorm, missing reason + custom.
    data_dir = _write_presets({
        "code_analyze": {"name": "Code Analyze", "temperature": 0.2,
                         "max_tokens": 8000, "system_prompt": "analyze"},
        "brainstorm": {"name": "Brainstorm", "temperature": 0.9,
                       "max_tokens": 4096, "system_prompt": "ideate"},
    })
    pm = PresetManager(data_dir)
    for key in PresetManager.DEFAULT_PRESETS:
        assert key in pm.presets, f"built-in preset {key!r} should be present"
    # The fill is persisted so the next load is already complete.
    with open(os.path.join(data_dir, "presets.json"), encoding="utf-8") as f:
        on_disk = json.load(f)
    assert "reason" in on_disk and "custom" in on_disk


def test_fill_does_not_clobber_user_edits():
    # An edited `custom` (enabled, bespoke prompt) plus a missing `reason`.
    edited_custom = {
        "name": "My Persona",
        "character_name": "My Persona",
        "temperature": 0.55,
        "max_tokens": 1234,
        "system_prompt": "You are my bespoke assistant.",
        "inject_prefix": "PRE",
        "inject_suffix": "SUF",
        "enabled": True,
    }
    data_dir = _write_presets({
        "code_analyze": {"name": "Code Analyze", "temperature": 0.2,
                         "max_tokens": 8000, "system_prompt": "analyze"},
        "brainstorm": {"name": "Brainstorm", "temperature": 0.9,
                       "max_tokens": 4096, "system_prompt": "ideate"},
        "custom": edited_custom,
        "user_templates": [{"id": "t1", "name": "Tmpl"}],
        # missing: reason
    })
    pm = PresetManager(data_dir)
    # reason was filled...
    assert "reason" in pm.presets
    # ...but the user's edited custom + templates are untouched.
    assert pm.presets["custom"] == edited_custom
    assert pm.presets["user_templates"] == [{"id": "t1", "name": "Tmpl"}]


def test_complete_file_is_not_rewritten_needlessly():
    # A file that already has every built-in must be returned unchanged.
    full = {k: dict(v) for k, v in PresetManager.DEFAULT_PRESETS.items()}
    full["custom"]["enabled"] = True  # a user edit that must survive
    data_dir = _write_presets(full)
    pm = PresetManager(data_dir)
    assert pm.presets["custom"]["enabled"] is True
    assert set(PresetManager.DEFAULT_PRESETS) <= set(pm.presets)
