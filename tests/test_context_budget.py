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


def test_hard_max_default_setting_matches_constant():
    # #1272 — the ceiling is now a named setting; its default must match the
    # historical module constant so behaviour is unchanged out of the box.
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["agent_input_token_hard_max"] == DEFAULT_HARD_MAX == 200000


def test_configurable_hard_max_raises_ceiling():
    # An admin lifting the cap lets a 1M-context model use more of its window.
    assert compute_input_token_budget(6000, 1_000_000, explicit=False, hard_max=500_000) == 500_000


def test_configurable_hard_max_lowers_ceiling():
    assert compute_input_token_budget(6000, 1_000_000, explicit=False, hard_max=50_000) == 50_000


def test_hard_max_ignored_when_explicit_budget_set():
    # The explicit branch ignores hard_max entirely (#1272 no-op note).
    assert compute_input_token_budget(6000, 1_000_000, explicit=True, hard_max=50_000) == 6000


def test_is_setting_overridden_reads_raw_saved_file(tmp_path, monkeypatch):
    import src.settings as settings

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"agent_input_token_budget": 12000}), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(f))
    assert settings.is_setting_overridden("agent_input_token_budget") is True
    assert settings.is_setting_overridden("some_unset_key") is False

    f.write_text(json.dumps({}), encoding="utf-8")
    assert settings.is_setting_overridden("agent_input_token_budget") is False
