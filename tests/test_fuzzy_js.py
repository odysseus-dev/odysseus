"""Pin the fuzzy scorer used by the command palette (Search Everywhere).

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_keybind_altgr_js.py /
test_compare_js.py). Skips when `node` is not installed rather than failing.

Tests pin RELATIVE ordering (boundary beats mid-word, consecutive beats
scattered, prefix beats non-prefix), not absolute scores, so the scoring
constants in fuzzy.js can be tuned without churning this file.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_FUZZY = _REPO / "static" / "js" / "fuzzy.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _score(query: str, target: str):
    """Return fuzzyScore(query, target) as a dict, or None."""
    js = f"""
    import {{ fuzzyScore }} from '{_FUZZY.as_uri()}';
    console.log(JSON.stringify(fuzzyScore({json.dumps(query)}, {json.dumps(target)})));
    """
    return json.loads(_run(js))


def _filter(query: str, titles: list):
    """Return fuzzyFilter over simple string items; yields [title, ...] sorted."""
    js = f"""
    import {{ fuzzyFilter }} from '{_FUZZY.as_uri()}';
    const items = {json.dumps(titles)};
    const out = fuzzyFilter({json.dumps(query)}, items, x => x);
    console.log(JSON.stringify(out.map(r => r.item)));
    """
    return json.loads(_run(js))


# --- subsequence requirement --------------------------------------------------

def test_non_subsequence_returns_null():
    assert _score("xyz", "Calendar") is None


def test_chars_out_of_order_rejected():
    # All chars present but not in order — not a subsequence.
    assert _score("lac", "Calendar") is None


def test_subsequence_matches_case_insensitively():
    r = _score("CAL", "calendar")
    assert r is not None
    assert r["positions"] == [0, 1, 2]


# --- relative ordering ---------------------------------------------------------

def test_word_boundary_beats_mid_word():
    # 'res' starts the word "Research" vs. lands mid-word in "Presets".
    boundary = _score("res", "Deep Research")["score"]
    mid_word = _score("res", "Presets")["score"]
    assert boundary > mid_word


def test_consecutive_beats_scattered():
    assert _score("abc", "abcdef")["score"] > _score("abc", "axbxcx")["score"]


def test_prefix_beats_non_prefix():
    assert _score("cal", "Calendar")["score"] > _score("cal", "My Calendar")["score"]


def test_camel_case_hump_counts_as_boundary():
    # 'r' on the camelCase hump of "deepResearch" beats a mid-word 'r'.
    hump = _score("r", "deepResearch")["score"]
    mid = _score("r", "presets")["score"]
    assert hump > mid


def test_filter_ranks_best_first():
    order = _filter("cal", ["Compare All", "Calendar", "Focus Message Input"])
    assert order[0] == "Calendar"
    assert "Focus Message Input" not in order  # contains no 'l' — not a subsequence


def test_filter_drops_non_matches():
    order = _filter("zzz", ["Calendar", "Notes"])
    assert order == []


# --- empty query ----------------------------------------------------------------

def test_empty_query_returns_all_in_registry_order():
    titles = ["Settings", "Theme", "Tasks"]
    assert _filter("", titles) == titles


def test_empty_query_scores_zero():
    js = f"""
    import {{ fuzzyFilter }} from '{_FUZZY.as_uri()}';
    const out = fuzzyFilter('', ['A', 'B'], x => x);
    console.log(JSON.stringify(out.map(r => [r.score, r.positions])));
    """
    assert json.loads(_run(js)) == [[0, []], [0, []]]


# --- positions (highlighting) ----------------------------------------------------

def test_positions_index_original_case_target():
    r = _score("dr", "Deep Research")
    assert r["positions"] == [0, 5]


def test_positions_consecutive_run():
    r = _score("cal", "Calendar")
    assert r["positions"] == [0, 1, 2]


# --- highlightRuns (XSS regression) -----------------------------------------------

def _runs(text: str, positions: list):
    js = f"""
    import {{ highlightRuns }} from '{_FUZZY.as_uri()}';
    console.log(JSON.stringify(highlightRuns({json.dumps(text)}, {json.dumps(positions)})));
    """
    return json.loads(_run(js))


def test_runs_concatenate_to_original_text():
    # The renderer assigns each run via textContent, so as long as runs are
    # plain substrings of the original title no markup can be injected.
    title = '<img src=x onerror=alert(1)>'
    runs = _runs(title, [0, 1, 2])
    assert "".join(r["text"] for r in runs) == title
    # An HTML-string renderer would have to escape; ours never parses, so the
    # runs carry the raw text verbatim and only the hl FLAG differs.
    assert [r["hl"] for r in runs[:2]] == [True, False]


def test_runs_merge_consecutive_and_clip_out_of_range():
    # positions beyond the title (keyword matches) and negatives are ignored.
    runs = _runs("abc", [1, 2, 5, -1])
    assert runs == [
        {"text": "a", "hl": False},
        {"text": "bc", "hl": True},
    ]


def test_runs_empty_positions_single_plain_run():
    assert _runs("Calendar", []) == [{"text": "Calendar", "hl": False}]
