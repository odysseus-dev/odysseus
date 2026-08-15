"""Tests for lessons.py — the teachable-moments wing.

Verifies the Reflexion-style design (episodic buffer + behavioural application):
- record stores a lesson as a chunk in the 'lessons' wing AND applies a
  behavioural delta (stored without applied behaviour would be a ledger,
  not a lesson)
- a lesson missing any of trigger/mistake/analysis/behaviour is refused
- recall retrieves relevant lessons (hybrid recall, wing-filtered)
- recent lists the latest lessons
- the wing label is 'lessons' so the plugin routes teachable moments there

Isolates to a temp store and temp deltas file; never touches real memory.
"""

import json
import os
import sqlite3
import sys

import pytest

_HERE = os.path.dirname(__file__)
# Works from both layouts: source repo (memory_platform/) and the private
# repo / deployed copy (flat scripts/).
for _sub in ("memory_platform", "scripts"):
    _p = os.path.join(_HERE, "..", _sub)
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break
import lessons  # noqa: E402
import growth_delta  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate the store DB, deltas file, and store writes for every test."""
    db = str(tmp_path / "mem.db")
    monkeypatch.setenv("MEMORY_STORE_DB", db)
    monkeypatch.setenv("MEMORY_MEMORY_DIR", str(tmp_path))

    # memory_store resolves DB_PATH/STORE_DIR at import — patch the constants
    # directly so connect() uses the temp store regardless of import order.
    import memory_store as ms
    monkeypatch.setattr(ms, "DB_PATH", db)
    monkeypatch.setattr(ms, "STORE_DIR", str(tmp_path / "store"))
    ms._embed.cache = {}  # never reuse embeddings across stores

    # growth_delta: temp file, and never reach the real store via subprocess.
    monkeypatch.setattr(growth_delta, "DELTAS_FILE", str(tmp_path / "gd.json"))
    monkeypatch.setattr(growth_delta, "_write_store", lambda text, topic: None)

    old_deltas = growth_delta.DELTAS_FILE
    yield tmp_path
    ms._embed.cache = {}
    growth_delta.DELTAS_FILE = old_deltas


def _lesson():
    return dict(
        trigger="when the persona is asked about a stored rule",
        mistake="claimed a user rule existed when it was a test fixture",
        analysis="the rule was never verified against the store before being cited",
        behaviour="verify a rule exists in the store before citing it",
        evidence="overnight growth test",
    )


def test_record_stores_and_applies(env):
    res = lessons.record(**_lesson())
    assert res["recorded"] is True
    assert res["behaviour_applied"] is True
    assert res["wing"] == "lessons"
    assert res["trigger"].startswith("when the persona")


def test_wing_is_lessons(env):
    lessons.record(**_lesson())
    import memory_store as ms
    db = ms.connect()
    row = db.execute("SELECT wing FROM chunks WHERE wing='lessons' "
                     "ORDER BY rowid DESC LIMIT 1").fetchone()
    db.close()
    assert row and row["wing"] == "lessons"


def test_lesson_missing_fields_refused(env):
    """The record guard refuses a lesson missing any required field. (CLI calls
    always pass all four flags, so empty strings are the real-world shape; the
    guard is what refuses them.)"""
    for kwargs in [
        {"trigger": "t", "mistake": "m", "analysis": "a", "behaviour": ""},  # no behaviour
        {"trigger": "t", "mistake": "m", "analysis": "", "behaviour": "b"},  # no analysis
        {"trigger": "", "mistake": "m", "analysis": "a", "behaviour": "b"},  # no trigger
        {"trigger": "t", "mistake": "", "analysis": "a", "behaviour": "b"},  # no mistake
    ]:
        res = lessons.record(**kwargs)
        assert res["recorded"] is False, f"expected refusal for {kwargs}"
        assert res["error"], "a refusal must name the missing fields"


def test_record_derives_behavioural_delta(env):
    """A stored lesson without an applied behavioural delta is a ledger, not
    a lesson — the mistake must change HOW the persona behaves."""
    lessons.record(**_lesson())
    state = growth_delta._load_deltas()
    applied = state.get("applied", [])
    assert applied, "no behavioural delta was journaled"
    assert "verify a rule exists" in applied[-1]["change"]


def test_recall_finds_relevant_lesson(env):
    lessons.record(**_lesson())
    rows = lessons.recall("stored rules and the persona", limit=5)
    assert rows, "recall returned nothing"
    assert any("verify a rule exists" in r["text"] for r in rows)


def test_recall_ranks_relevant_lesson_first(env):
    """Broad recall (min_sim=0) returns chunks regardless of topic, so the
    meaningful check is RANKING: a related query must surface the matching
    lesson before an unrelated one. Seed a rule lesson + an image-rendering
    lesson; a rule query ranks the rule lesson first."""
    lessons.record(**{**_lesson(), "trigger": "when handling claims about rules"})
    lessons.record(**{**_lesson(), "trigger": "when rendering images from prompts",
                      "mistake": "rendered a scene with wrong camera framing",
                      "analysis": "the render config was not verified first",
                      "behaviour": "check render params before generating"})
    rows = lessons.recall("verify a stored rule before citing it", limit=3)
    assert rows, "no lessons recalled"
    assert "rule exists" in rows[0]["text"], \
        f"the rule lesson must rank first for a rule query: {rows}"


def test_recent_lists_latest(env):
    lessons.record(**{**_lesson(), "trigger": "first lesson trigger about storage"})
    lessons.record(**{**_lesson(), "trigger": "second lesson trigger about recall"})
    rows = lessons.recent(limit=2)
    assert len(rows) == 2
    # newest first
    assert "second lesson trigger" in rows[0]["text"]


def test_cli_record_roundtrip(env):
    """The CLI surface works end to end (what the plugin invokes)."""
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(lessons.__file__), "lessons.py"),
         "record", "cli trigger lesson", "--mistake", "cli mistake",
         "--analysis", "cli analysis", "--behaviour", "cli behaviour",
         "--json"],
        capture_output=True, text=True, timeout=60, env=os.environ.copy())
    out = json.loads(r.stdout or "{}")
    assert out.get("recorded") is True, r.stdout + r.stderr
    assert out.get("behaviour_applied") is True
