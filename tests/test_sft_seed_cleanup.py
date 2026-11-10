from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hygiene_rejects_meta_marker_and_raw_answer() -> None:
    module = load_script("filter_sft_seed_hygiene.py")
    rows = [{
        "user": "Run the SFT fixture {marker}",
        "assistant": "x" * 501,
        "tool_events": [],
    }]

    assert module.reasons_for_session(rows) == [
        "long_answer_without_tool",
        "marker_or_run_id",
        "meta_user",
    ]


def test_hygiene_keeps_natural_tool_trace() -> None:
    module = load_script("filter_sft_seed_hygiene.py")
    rows = [{
        "user": "Show my calendar for September",
        "assistant": "You have three events in September.",
        "tool_events": [{"tool": "manage_calendar", "command": {"action": "list_events"}}],
    }]

    assert module.reasons_for_session(rows) == []
