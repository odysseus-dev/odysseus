"""Issue #1170 — the agent input-token budget adapts to the model context window.

Pins the pure budget computation and the explicit-override detection.
"""

import json

from src.context_budget import compute_input_token_budget, DEFAULT_HARD_MAX


def test_default_scales_to_context_window():
    # Not explicit, big window -> ~85% of the window (the old code capped at 6000).
    assert compute_input_token_budget(6000, 128000, explicit=False) == int(128000 * 0.85)


def test_default_capped_at_hard_max_for_huge_windows():
    assert compute_input_token_budget(6000, 1_000_000, explicit=False) == DEFAULT_HARD_MAX


def test_explicit_budget_is_honoured():
    # User explicitly chose 6000 -> keep it even on a 128K model.
    assert compute_input_token_budget(6000, 128000, explicit=True) == 6000
    # A larger explicit budget is honoured too, clamped to the window.
    assert compute_input_token_budget(50000, 128000, explicit=True) == 50000


def test_explicit_budget_clamped_to_window():
    assert compute_input_token_budget(200000, 32000, explicit=True) == 32000


def test_unknown_window_falls_back_to_configured():
    assert compute_input_token_budget(6000, 0, explicit=False) == 6000
    assert compute_input_token_budget(0, 0, explicit=False) == 6000  # default


def test_is_setting_overridden_reads_raw_saved_file(tmp_path, monkeypatch):
    import src.settings as settings

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"agent_input_token_budget": 12000}), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(f))
    assert settings.is_setting_overridden("agent_input_token_budget") is True
    assert settings.is_setting_overridden("some_unset_key") is False

    f.write_text(json.dumps({}), encoding="utf-8")
    assert settings.is_setting_overridden("agent_input_token_budget") is False


# ---------------------------------------------------------------------------
# Configurable hard_max — the ceiling on the auto-derived path is a setting
# (`agent_input_token_hard_max`), not a hidden constant. History: a reviewer
# required it on #1190, the merged #1230 shipped without it, and #1273 added it.
# This test pins the function-level override (the `hard_max` parameter); without
# a raisable ceiling, admins on 1M+ context APIs would be stuck at the 200K default.
# ---------------------------------------------------------------------------

def test_custom_hard_max_overrides_default_in_auto_branch():
    """A caller-supplied hard_max lifts the auto-derived ceiling."""
    # Without override: 1M ctx -> capped at DEFAULT_HARD_MAX (200K)
    assert compute_input_token_budget(6000, 1_000_000, explicit=False) == DEFAULT_HARD_MAX
    # With explicit raise: 1M ctx -> 850K (85% of 1M), under the raised ceiling
    assert compute_input_token_budget(6000, 1_000_000, explicit=False, hard_max=900_000) == int(1_000_000 * 0.85)


def test_custom_hard_max_lowers_default_for_cost_paranoid_setups():
    """A lower ceiling caps the auto-derived budget below the default."""
    # 128K ctx, default ceiling 200K -> 85% of 128K = 108800
    assert compute_input_token_budget(6000, 128_000, explicit=False) == int(128_000 * 0.85)
    # Same ctx, ceiling lowered to 50K -> capped at 50K instead
    assert compute_input_token_budget(6000, 128_000, explicit=False, hard_max=50_000) == 50_000


def test_hard_max_has_no_effect_on_explicit_branch():
    """When the user set an explicit budget, hard_max must not silently cap it."""
    # User chose 900K explicitly; ctx is 1M; ceiling is 100K — user's choice wins.
    assert compute_input_token_budget(900_000, 1_000_000, explicit=True, hard_max=100_000) == 900_000


def test_default_settings_registers_hard_max_key():
    """Required so /api/auth/settings and manage_settings can persist the key."""
    from src.settings import DEFAULT_SETTINGS
    assert "agent_input_token_hard_max" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["agent_input_token_hard_max"] == DEFAULT_HARD_MAX


def test_alias_map_registers_friendly_names():
    """`manage_settings` should accept 'hard max' and friends."""
    from pathlib import Path
    # manage_settings (and its alias map) moved to agent_tools/admin_tools.py in #3629.
    src = Path("src/agent_tools/admin_tools.py").read_text()
    assert '"hard max": "agent_input_token_hard_max"' in src
    assert '"token budget cap": "agent_input_token_hard_max"' in src
    assert '"input budget cap": "agent_input_token_hard_max"' in src


def test_agent_loop_reads_hard_max_setting(tmp_path, monkeypatch):
    """End-to-end: a saved settings.json value for agent_input_token_hard_max
    must reach compute_input_token_budget on the real agent_loop call path."""
    import src.settings as settings
    # Point SETTINGS_FILE at a temp file with our override.
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"agent_input_token_hard_max": 750_000}), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(f))
    monkeypatch.setattr(settings, "_settings_cache", None)
    # Read via the same import path the agent loop uses.
    assert settings.get_setting("agent_input_token_hard_max", DEFAULT_HARD_MAX) == 750_000

    # Malformed value falls back to DEFAULT_HARD_MAX (defensive, matches the
    # try/except in src/agent_loop.py).
    f.write_text(json.dumps({"agent_input_token_hard_max": "huge"}), encoding="utf-8")
    monkeypatch.setattr(settings, "_settings_cache", None)
    raw = settings.get_setting("agent_input_token_hard_max", DEFAULT_HARD_MAX)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = DEFAULT_HARD_MAX
    if parsed <= 0:
        parsed = DEFAULT_HARD_MAX
    assert parsed == DEFAULT_HARD_MAX
