"""Regression for issue #1809 — deleting a document in the Library didn't
decrease the "(N documents)" stat or the "all (N)" / per-language chips. Those
counters are computed from the language-facet map (_libraryLanguages), which the
optimistic single-delete path updated _libraryDocs/_libraryTotal but never
decremented — so libraryRenderStats() re-rendered the same stale total and the
chips weren't refreshed at all.

The facet decrement lives in static/js/docLibraryFacets.js (pure, no DOM) and is
executed under node here; the wiring in documentLibrary.js is guarded at source.
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script):
    res = subprocess.run(["node", "--input-type=module", "-e", script],
                         cwd=_REPO, capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(res.stderr)
    return json.loads([ln for ln in res.stdout.splitlines() if ln.strip()][-1])


def test_forget_doc_language_decrements_and_prunes(node_available):
    script = textwrap.dedent("""
        const { forgetDocLanguage } = await import('./static/js/docLibraryFacets.js');
        const out = {};
        out.decrement   = forgetDocLanguage({python: 3, text: 2}, 'python');
        out.prune_zero  = forgetDocLanguage({python: 1, text: 2}, 'python'); // python->0 dropped
        out.null_bucket = forgetDocLanguage({text: 2}, null);   // NULL -> text bucket
        out.empty_bucket= forgetDocLanguage({text: 2}, '');     // '' -> text bucket
        out.text_bucket = forgetDocLanguage({text: 1}, 'text'); // text->0 dropped
        out.unknown_key = forgetDocLanguage({python: 1}, 'go'); // not present -> unchanged
        // input is not mutated
        const orig = {python: 2};
        forgetDocLanguage(orig, 'python');
        out.no_mutation = orig;
        console.log(JSON.stringify(out));
    """)
    out = _run_node(script)
    assert out["decrement"] == {"python": 2, "text": 2}
    assert out["prune_zero"] == {"text": 2}
    assert out["null_bucket"] == {"text": 1}
    assert out["empty_bucket"] == {"text": 1}
    assert out["text_bucket"] == {}
    assert out["unknown_key"] == {"python": 1}
    assert out["no_mutation"] == {"python": 2}


def test_single_delete_refreshes_stat_and_chips():
    """libraryDeleteSingle must decrement the facet map AND re-render both the
    stat line and the chips — not just the grid (the #1809 gap)."""
    src = (_REPO / "static/js/documentLibrary.js").read_text(encoding="utf-8")
    start = src.index("async function libraryDeleteSingle")
    body = src[start: start + 1400]
    assert "forgetDocLanguage(_libraryLanguages" in body, "must decrement the facet map"
    assert "libraryRenderStats()" in body
    assert "libraryRenderLangChips()" in body, "must refresh the chips, not only the stat"
