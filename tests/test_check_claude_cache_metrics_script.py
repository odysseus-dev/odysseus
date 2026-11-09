"""Behavior of scripts/check_claude_cache_metrics.py against a small fake app.db.

The rows mirror a real 3-turn claude-sonnet-4-6 chat: turn 1 saves no
top-level cache read (None) but repeats its cache write in usage_buckets,
and context_percent is rounded while context_length holds the exact window.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_claude_cache_metrics.py"


def _turn(read, write, req_tokens, buckets):
    return {
        "model": "claude-sonnet-4-6",
        "input_tokens": req_tokens,
        "output_tokens": 16,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
        "context_length": 1_000_000,
        "context_percent": 0.2,
        "request_context_tokens": req_tokens,
        "cost_usd": 0.004,
        "usage_buckets": buckets,
    }


def _make_db(path, turns):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE chat_messages (session_id TEXT, timestamp TEXT, role TEXT, metadata TEXT)")
    for i, metrics in enumerate(turns):
        con.execute(
            "INSERT INTO chat_messages VALUES (?, ?, 'assistant', ?)",
            ("s1", f"2026-11-09T05:17:0{i}", json.dumps(metrics)),
        )
    con.commit()
    con.close()


def _run(db):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(db)],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _row(out, n):
    return next(line for line in out.splitlines() if line.strip().startswith(f"{n} ")).split()


def test_buckets_are_not_added_on_top_of_top_level_cache_fields(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, [
        _turn(None, 2214, 2217, [{"cache_read_input_tokens": None, "cache_creation_input_tokens": 2214}]),
        _turn(1221, 1020, 2244, [{"cache_read_input_tokens": 1221, "cache_creation_input_tokens": 1020}]),
        _turn(1221, 1047, 2271, [{"cache_read_input_tokens": 1221, "cache_creation_input_tokens": 1047}]),
    ])
    out = _run(db)
    # columns: #, model, input, cache_read, cache_write, cost, ctx%, window
    assert _row(out, 1)[3:5] == ["0", "2214"]
    assert _row(out, 2)[3:5] == ["1221", "1020"]
    assert "Cache reads on round 2+:          PASS" in out


def test_window_uses_exact_context_length_not_rounded_percent(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, [_turn(None, 2214, 2217, []), _turn(1221, 1020, 2244, [])])
    out = _run(db)
    assert _row(out, 1)[7] == "1000000"
    assert _row(out, 2)[7] == "1000000"
    assert "Context window about 1M:         PASS" in out


def test_buckets_still_used_when_no_top_level_cache_fields(tmp_path):
    db = tmp_path / "app.db"
    older = _turn(None, None, 2244, [{"cache_read_input_tokens": 1221, "cache_creation_input_tokens": 1020}])
    _make_db(db, [_turn(None, None, 2217, []), older])
    out = _run(db)
    assert _row(out, 2)[3:5] == ["1221", "1020"]
