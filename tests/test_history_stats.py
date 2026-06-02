"""Unit tests for the /api/history/stats aggregation helpers.

Exercises the pure helpers `_parse_window` and `_compute_stats` from
`routes.history_routes` without touching SQLAlchemy or the HTTP layer.
Uses the same import-time stubbing trick as test_auth_regressions.py so
the route module imports cleanly under the conftest mocks.
"""

import os
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock


def _ensure_stub(name: str, **attrs):
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        if parent_name not in sys.modules:
            parent = types.ModuleType(parent_name)
            real_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                *parent_name.split("."),
            )
            parent.__path__ = [real_path] if os.path.isdir(real_path) else []
            sys.modules[parent_name] = parent
        else:
            parent = sys.modules[parent_name]
    else:
        parent = None
        child_name = None

    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    if parent is not None and not hasattr(parent, child_name):
        setattr(parent, child_name, mod)
    return mod


_ensure_stub("core.database",
    SessionLocal=MagicMock(), Session=MagicMock(), ChatMessage=MagicMock(),
)
_ensure_stub("core.models", ChatMessage=MagicMock())
_ensure_stub("src.topic_analyzer", analyze_topics=MagicMock())
_ensure_stub("routes.session_routes", _verify_session_owner=MagicMock())

from routes.history_routes import _parse_window, _compute_stats  # noqa: E402


def make_sess(model="m", mode=None, input_tok=0, output_tok=0, messages=0,
              date="2026-06-01", sid="s1", name="Chat"):
    return SimpleNamespace(
        id=sid, name=name, model=model, mode=mode,
        total_input_tokens=input_tok, total_output_tokens=output_tok,
        message_count=messages, last_message_at=datetime(2026, 6, 1),
        created_at=datetime.fromisoformat(date),
    )


# --- _parse_window -------------------------------------------------------

def test_parse_window_none():
    assert _parse_window(None) is None


def test_parse_window_all():
    assert _parse_window("all") is None


def test_parse_window_7d():
    out = _parse_window("7d")
    expected = datetime.utcnow() - timedelta(days=7)
    assert out is not None
    assert abs((out - expected).total_seconds()) < 5


def test_parse_window_30d():
    out = _parse_window("30d")
    expected = datetime.utcnow() - timedelta(days=30)
    assert out is not None
    assert abs((out - expected).total_seconds()) < 5


def test_parse_window_bad_input():
    assert _parse_window("bad_input") is None


# --- _compute_stats ------------------------------------------------------

def test_compute_stats_empty():
    out = _compute_stats([], "all", False)
    assert out["sessions"] == 0
    assert out["messages"] == 0
    assert out["input_tokens"] == 0
    assert out["output_tokens"] == 0
    assert out["total_tokens"] == 0
    assert out["top_models"] == []
    assert out["top_sessions"] == []
    assert "daily" not in out


def test_compute_stats_totals_and_top_models_sorted():
    sessions = [
        make_sess(model="small", input_tok=100, output_tok=50, messages=3),
        make_sess(model="big", input_tok=1000, output_tok=500, messages=10),
    ]
    out = _compute_stats(sessions, "7d", True)
    assert out["period"] == "7d"
    assert out["sessions"] == 2
    assert out["messages"] == 13
    assert out["input_tokens"] == 1100
    assert out["output_tokens"] == 550
    assert out["total_tokens"] == 1650
    # sorted by input+output desc → big first
    assert [m["model"] for m in out["top_models"]] == ["big", "small"]
    assert out["top_models"][0]["sessions"] == 1


def test_compute_stats_by_mode_null_maps_to_chat():
    sessions = [
        make_sess(mode="agent", input_tok=180000, output_tok=27000),
        make_sess(mode=None, input_tok=90000, output_tok=14000),
        make_sess(mode="agent", input_tok=10, output_tok=5),
    ]
    out = _compute_stats(sessions, "all", False)
    by_mode = out["by_mode"]
    assert set(by_mode.keys()) == {"agent", "chat"}
    assert by_mode["agent"]["sessions"] == 2
    assert by_mode["agent"]["input_tokens"] == 180010
    assert by_mode["chat"]["sessions"] == 1
    assert by_mode["chat"]["input_tokens"] == 90000


def test_compute_stats_top_sessions_capped_and_sorted():
    sessions = [
        make_sess(sid=f"s{i}", input_tok=i * 100, output_tok=0, messages=i)
        for i in range(1, 8)  # 7 sessions, tokens 100..700
    ]
    out = _compute_stats(sessions, "all", False)
    top = out["top_sessions"]
    assert len(top) == 5
    # highest tokens first: s7 (700) .. s3 (300)
    assert [s["id"] for s in top] == ["s7", "s6", "s5", "s4", "s3"]
    assert top[0]["input_tokens"] == 700


def test_compute_stats_daily_bucketing():
    sessions = [
        make_sess(sid="a", input_tok=45000, output_tok=7200, date="2026-06-01"),
        make_sess(sid="b", input_tok=28000, output_tok=4300, date="2026-06-02"),
        make_sess(sid="c", input_tok=1000, output_tok=200, date="2026-06-02"),
    ]
    out = _compute_stats(sessions, "7d", True)
    daily = out["daily"]
    assert [d["date"] for d in daily] == ["2026-06-01", "2026-06-02"]
    assert daily[0]["sessions"] == 1
    assert daily[0]["input_tokens"] == 45000
    assert daily[1]["sessions"] == 2
    assert daily[1]["input_tokens"] == 29000
    assert daily[1]["output_tokens"] == 4500


def test_compute_stats_missing_token_values_treated_as_zero():
    s = SimpleNamespace(
        id="x", name="n", model="m", mode="chat",
        total_input_tokens=None, total_output_tokens=None,
        message_count=None, last_message_at=None, created_at=datetime(2026, 6, 1),
    )
    out = _compute_stats([s], "all", False)
    assert out["input_tokens"] == 0
    assert out["output_tokens"] == 0
    assert out["messages"] == 0
    assert out["top_sessions"][0]["last_message_at"] is None
