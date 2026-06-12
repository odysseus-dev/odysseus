"""Regression: long-context models were silently re-capped at the 6000-token
default agent_input_token_budget even though #1230 added context-window scaling.

Root cause: the settings-save path materializes every DEFAULT_SETTINGS key into
settings.json (load_settings merges defaults; handlers persist the merged dict),
so the persisted default 6000 made is_setting_overridden() return True. #1230's
budget code read that as an explicit user choice and took the `min(6000, ctx)`
branch — defeating the scaling for anyone who had ever saved a setting.

Fix: gate the scaling on is_setting_customized() (saved value != default) instead
of is_setting_overridden() (mere presence). A persisted default is not a choice.
"""

import json
from unittest.mock import patch

import src.settings as s
from src.context_budget import compute_input_token_budget, DEFAULT_HARD_MAX

DEFAULT_BUDGET = s.DEFAULT_SETTINGS["agent_input_token_budget"]  # 6000
CTX = 131072  # nemotron-nano-12b-vl window


def _write(tmp_path, obj):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(obj))
    s._settings_cache = None
    return str(p)


def test_materialized_default_is_not_customized(tmp_path):
    """The exact regression: default value persisted to disk must NOT count as a
    deliberate user choice."""
    path = _write(tmp_path, {"agent_input_token_budget": DEFAULT_BUDGET})
    with patch.object(s, "SETTINGS_FILE", path):
        assert s.is_setting_overridden("agent_input_token_budget") is True   # present...
        assert s.is_setting_customized("agent_input_token_budget") is False  # ...but == default


def test_nondefault_value_is_customized(tmp_path):
    path = _write(tmp_path, {"agent_input_token_budget": 50000})
    with patch.object(s, "SETTINGS_FILE", path):
        assert s.is_setting_customized("agent_input_token_budget") is True


def test_absent_key_is_not_customized(tmp_path):
    path = _write(tmp_path, {"some_other_key": 1})
    with patch.object(s, "SETTINGS_FILE", path):
        assert s.is_setting_customized("agent_input_token_budget") is False


def test_budget_scales_when_default_materialized(tmp_path):
    """End-to-end: materialized default -> not customized -> scaling runs ->
    long-context model gets ~85% of its window, not 6000."""
    path = _write(tmp_path, {"agent_input_token_budget": DEFAULT_BUDGET})
    with patch.object(s, "SETTINGS_FILE", path):
        explicit = s.is_setting_customized("agent_input_token_budget")
    budget = compute_input_token_budget(DEFAULT_BUDGET, CTX, explicit, hard_max=DEFAULT_HARD_MAX)
    assert budget > 100_000, f"expected scaled budget, got {budget}"


def test_explicit_nondefault_budget_is_honoured(tmp_path):
    """A deliberately-chosen budget is still respected (clamped to the window)."""
    path = _write(tmp_path, {"agent_input_token_budget": 20000})
    with patch.object(s, "SETTINGS_FILE", path):
        explicit = s.is_setting_customized("agent_input_token_budget")
    budget = compute_input_token_budget(20000, CTX, explicit, hard_max=DEFAULT_HARD_MAX)
    assert budget == 20000
