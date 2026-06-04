"""Regression: params_b must not crash the ranking pass on a malformed count.

`parameter_count` is matched with `^([\\d.]+)\\s*([BKMGT]?)$`. The `[\\d.]+`
class happily matches a multi-dot value like "1.5.3B", but `float("1.5.3")`
raises ValueError. params_b is called for every model in analyze_model/
rank_models, so one bad catalog row aborted the entire ranking request. A
malformed count is now treated as unknown size (0.0) instead of raising.
"""
from services.hwfit.models import params_b


def test_malformed_multidot_count_does_not_raise():
    assert params_b({"parameter_count": "1.5.3B"}) == 0.0
    assert params_b({"parameter_count": "7.0.1B"}) == 0.0


def test_valid_counts_still_parse():
    assert params_b({"parameter_count": "7B"}) == 7.0
    assert params_b({"parameter_count": "70B"}) == 70.0
    assert params_b({"parameter_count": "355M"}) == 0.355


def test_raw_param_count_preferred():
    assert params_b({"parameters_raw": 7_000_000_000}) == 7.0
