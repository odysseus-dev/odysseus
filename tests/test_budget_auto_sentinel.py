"""Agent input-token budget contract (review on #4122).

- The DEFAULT value is the AUTO sentinel: it scales to the model's context window.
  Any non-default value is an explicit cap. A materialized default 6000 can't be
  told apart from a deliberate 6000 (the settings-save path persists defaults), so
  the default reads as auto — pin a cap with a nearby value (e.g. 5999).
- Auto-scaling only trusts a DISCOVERED context window; a bare DEFAULT_CONTEXT
  fallback stays conservative instead of scaling off an unproven window.
"""

import json
from unittest.mock import patch

import src.settings as settings
import src.model_context as mc
from src.context_budget import compute_input_token_budget, DEFAULT_BUDGET, budget_is_explicit


def test_default_value_is_the_auto_sentinel():
    # The settings default equals DEFAULT_BUDGET, so the agent loop (which compares
    # the configured value to DEFAULT_BUDGET) treats the default as "auto".
    assert settings.DEFAULT_SETTINGS["agent_input_token_budget"] == DEFAULT_BUDGET


def test_saving_an_unrelated_setting_does_not_re_cap_the_budget(tmp_path, monkeypatch):
    """End-to-end regression (WGlynn, #4121): changing ANY setting makes the
    settings-save path persist the merged dict, which materializes the budget
    default into settings.json. The budget must still AUTO-SCALE — it must not be
    re-read as an explicit 6000 cap. This locks the exact reopening shut.
    """
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._settings_cache = None

    # Simulate a real settings save: a handler loads the merged dict (defaults +
    # saved) and persists it after the user changes one *unrelated* setting.
    merged = settings.load_settings()
    merged["search_result_count"] = 9                  # unrelated user change
    settings.save_settings(merged)
    settings._settings_cache = None

    # The budget default is now physically materialized into the file...
    raw = json.loads(settings_file.read_text())
    assert raw["agent_input_token_budget"] == DEFAULT_BUDGET
    assert raw["search_result_count"] == 9

    # ...yet it must read as AUTO (value == default), not an explicit cap — even
    # though is_setting_overridden would report True for it now.
    assert settings.is_setting_overridden("agent_input_token_budget") is True
    soft = int(settings.get_setting("agent_input_token_budget", DEFAULT_BUDGET) or 0)
    assert budget_is_explicit(soft) is False
    # And the effective budget scales to the window rather than capping at 6000.
    assert compute_input_token_budget(soft, 131072, explicit=budget_is_explicit(soft)) == int(131072 * 0.85)


def test_auto_scales_on_a_known_window():
    assert compute_input_token_budget(DEFAULT_BUDGET, 131072, explicit=False) == int(131072 * 0.85)


def test_auto_stays_conservative_on_unknown_window():
    # P2 #2: the budget block passes context_length=0 when the window is only a
    # fallback, so auto-scaling must NOT inflate to the unproven window.
    assert compute_input_token_budget(DEFAULT_BUDGET, 0, explicit=False) == DEFAULT_BUDGET


def test_nondefault_value_is_an_explicit_cap():
    assert compute_input_token_budget(20000, 131072, explicit=True) == 20000      # honoured
    assert compute_input_token_budget(200000, 32000, explicit=True) == 32000      # clamped to window


def test_get_context_length_known_surfaces_endpoint_proven_vs_fallback():
    mc._context_cache.clear()
    with patch.object(mc, "_query_context_length", return_value=(131072, True)):
        assert mc.get_context_length_known("http://proven/v1", "m1") == (131072, True)
    mc._context_cache.clear()
    with patch.object(mc, "_query_context_length", return_value=(mc.DEFAULT_CONTEXT, False)):
        ctx, known = mc.get_context_length_known("http://unknown/v1", "m2")
        assert ctx == mc.DEFAULT_CONTEXT and known is False
    # get_context_length keeps its plain-int contract for existing callers
    mc._context_cache.clear()
    with patch.object(mc, "_query_context_length", return_value=(64000, True)):
        assert mc.get_context_length("http://proven/v1", "m3") == 64000


def test_budget_context_binds_known_flag_to_its_own_value():
    """Regression (RaresKeY, #4122): scale the budget off the value the `known`
    flag actually proves — never a stale/missing context_length from a different
    lookup. Covers the local-restaleness case (fresh proven value beats a stale
    fallback) and the no-arg-caller case (discovers a long window despite fallback=0).
    """
    # unknown / bare fallback -> 0 (don't scale off an unproven window)
    with patch.object(mc, "get_context_length_known", return_value=(128000, False)):
        assert mc.budget_context_for_model("u", "m", fallback=128000) == 0
    # known -> the freshly-proven value, NOT the (stale) fallback the caller passed
    with patch.object(mc, "get_context_length_known", return_value=(4096, True)):
        assert mc.budget_context_for_model("u", "m", fallback=128000) == 4096
    # no-arg caller (fallback=0) still gets the discovered long window
    with patch.object(mc, "get_context_length_known", return_value=(131072, True)):
        assert mc.budget_context_for_model("u", "m", fallback=0) == 131072
    # probe error -> caller's fallback (prior behaviour)
    with patch.object(mc, "get_context_length_known", side_effect=RuntimeError):
        assert mc.budget_context_for_model("u", "m", fallback=4096) == 4096


def test_no_arg_caller_scales_from_discovered_window_not_6000():
    """End-to-end of the fix: a caller that passes no context_length (scheduled
    tasks, teacher escalation, ...) but whose endpoint reports 131072 now scales to
    ~111k instead of being capped at the conservative 6000."""
    with patch.object(mc, "get_context_length_known", return_value=(131072, True)):
        ctx = mc.budget_context_for_model("u", "m", fallback=0)
    assert compute_input_token_budget(DEFAULT_BUDGET, ctx, explicit=False) == int(131072 * 0.85)
