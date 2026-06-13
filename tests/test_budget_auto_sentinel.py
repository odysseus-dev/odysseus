"""Agent input-token budget contract (review on #4122).

- The DEFAULT value is the AUTO sentinel: it scales to the model's context window.
  Any non-default value is an explicit cap. A materialized default 6000 can't be
  told apart from a deliberate 6000 (the settings-save path persists defaults), so
  the default reads as auto — pin a cap with a nearby value (e.g. 5999).
- Auto-scaling only trusts a DISCOVERED context window; a bare DEFAULT_CONTEXT
  fallback stays conservative instead of scaling off an unproven window.
"""

from unittest.mock import patch

import src.settings as settings
import src.model_context as mc
from src.context_budget import compute_input_token_budget, DEFAULT_BUDGET


def test_default_value_is_the_auto_sentinel():
    # The settings default equals DEFAULT_BUDGET, so the agent loop (which compares
    # the configured value to DEFAULT_BUDGET) treats the default as "auto".
    assert settings.DEFAULT_SETTINGS["agent_input_token_budget"] == DEFAULT_BUDGET


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
