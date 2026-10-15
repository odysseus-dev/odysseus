"""Fire-time prompt placeholder expansion (``{since_ts}``).

The token must resolve at execution with the window it carries, never
freeze at reconcile time, clamp to sane bounds, and leave unrelated
braces alone.
"""

from src.task_scheduler import _expand_live_prompt_placeholders


def test_bare_token_defaults_to_24h():
    out = _expand_live_prompt_placeholders("cut {since_ts} end")
    val = int(out.split()[1])
    import time
    assert abs(val - (int(time.time()) - 24 * 3600)) < 5


def test_carried_window_is_honored():
    out = _expand_live_prompt_placeholders("cut {since_ts:48} end")
    val = int(out.split()[1])
    import time
    assert abs(val - (int(time.time()) - 48 * 3600)) < 5


def test_window_clamped_to_168h():
    out = _expand_live_prompt_placeholders("cut {since_ts:999} end")
    val = int(out.split()[1])
    import time
    assert abs(val - (int(time.time()) - 168 * 3600)) < 5


def test_window_clamped_to_1h():
    out = _expand_live_prompt_placeholders("cut {since_ts:0} end")
    val = int(out.split()[1])
    import time
    assert abs(val - (int(time.time()) - 3600)) < 5


def test_multiple_tokens_all_resolve():
    import re, time
    out = _expand_live_prompt_placeholders("{since_ts} x {since_ts:12} y")
    vals = [int(v) for v in re.findall(r"\d{6,}", out)]
    assert len(vals) == 2, out
    assert abs(vals[0] - (int(time.time()) - 24 * 3600)) < 5
    assert abs(vals[1] - (int(time.time()) - 12 * 3600)) < 5


def test_unknown_placeholders_untouched():
    out = _expand_live_prompt_placeholders("keep {foo} and {lookback_hours} as-is")
    assert out == "keep {foo} and {lookback_hours} as-is"
