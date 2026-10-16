"""Guarded resolution of scheduled-research settings (#5049).

The scheduler reads ``research_max_tokens`` /
``research_extraction_timeout_seconds`` / ``research_extraction_concurrency``
from settings.json with bare ``int()`` casts. A hand-edited value like
``{"research_max_tokens": "invalid"}`` used to raise inside
``TaskScheduler._execute_research_task`` and kill every scheduled research
run until settings.json was fixed by hand.

``_resolve_research_settings`` must fall back to the documented defaults
(8192 / 90 / 3) for non-numeric input while preserving valid-value behavior
(numeric strings still parse). ``src/research_handler.py`` already guards the
same reads defensively; this is the scheduler's equivalent.
"""

from __future__ import annotations

from src.task_scheduler import _resolve_research_settings


def _fake_get_setting(values):
    """A get_setting stand-in backed by a plain dict."""

    def get(key, default=None):
        return values.get(key, default)

    return get


def test_non_numeric_values_fall_back_to_documented_defaults():
    get = _fake_get_setting(
        {
            "research_max_tokens": "invalid",
            "research_extraction_timeout_seconds": "bogus",
            "research_extraction_concurrency": "not-a-number",
        }
    )

    assert _resolve_research_settings(get) == (8192, 90, 3)


def test_none_values_fall_back_to_documented_defaults():
    get = _fake_get_setting(
        {
            "research_max_tokens": None,
            "research_extraction_timeout_seconds": None,
            "research_extraction_concurrency": None,
        }
    )

    assert _resolve_research_settings(get) == (8192, 90, 3)


def test_numeric_strings_still_parse():
    get = _fake_get_setting(
        {
            "research_max_tokens": "4096",
            "research_extraction_timeout_seconds": "45",
            "research_extraction_concurrency": "2",
        }
    )

    assert _resolve_research_settings(get) == (4096, 45, 2)


def test_missing_keys_keep_documented_defaults():
    assert _resolve_research_settings(_fake_get_setting({})) == (8192, 90, 3)


def test_valid_ints_pass_through():
    get = _fake_get_setting(
        {
            "research_max_tokens": 2048,
            "research_extraction_timeout_seconds": 120,
            "research_extraction_concurrency": 6,
        }
    )

    assert _resolve_research_settings(get) == (2048, 120, 6)
