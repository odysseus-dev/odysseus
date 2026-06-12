"""Regression tests for issue #1551 — deep research reported "No information
could be gathered" and showed nothing, even though the search rounds had already
extracted findings.

Two root causes in src/deep_research.py:

1. `_synthesize` hard-capped its LLM call at `timeout=60`, while extraction uses
   the user's `extraction_timeout` (e.g. 300s) and the final report uses 180s. A
   slow local model (the reporter served a 20B from LM Studio) needs >60s to
   synthesize a round's findings, so synthesis timed out after 3 attempts.

2. When synthesis failed on the first round, the gathered findings were thrown
   away: `if not report: return "No information could be gathered…"`. The 8
   findings the run had already extracted were lost.

The fixes: give synthesis the same 180s budget as the final report, and fall
back to a compiled report built from the gathered findings when synthesis
produced nothing. These run without a live LLM or DB (same stub pattern as
tests/test_deep_research_date_context.py).
"""
import asyncio

from src.deep_research import DeepResearcher


def _researcher():
    # Build without the heavy __init__; the methods under test only need these.
    r = DeepResearcher.__new__(DeepResearcher)
    r.synthesis_window = 10
    r.max_report_tokens = 4096
    return r


_FINDINGS = [
    {"url": "https://ex.com/a", "title": "Diarization basics",
     "summary": "Speaker diarization segments audio by speaker identity."},
    {"url": "https://ex.com/b", "title": "x-vectors",
     "evidence": "x-vectors are embeddings used to cluster speech segments."},
]


def test_synthesis_uses_a_generous_timeout_not_60s():
    """The synthesis LLM call must get a budget consistent with the final report
    (180s), not the old 60s that timed out on slow local models (#1551)."""
    r = _researcher()
    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen.update(kwargs)
        return "synthesized report"

    r._llm = _fake_llm
    r._emit = lambda **k: None

    out = asyncio.run(r._synthesize("q", _FINDINGS, ""))
    assert out == "synthesized report"
    assert seen.get("timeout", 0) >= 180, f"synthesis timeout too short: {seen.get('timeout')}"


def test_fallback_report_preserves_findings():
    """_fallback_report must surface the gathered findings (title + content),
    not a 'nothing found' message."""
    r = _researcher()
    report = r._fallback_report("how does speaker diarization work", _FINDINGS)
    assert "speaker diarization" in report.lower()
    assert "Diarization basics" in report
    assert "x-vectors" in report
    assert "https://ex.com/a" in report
    # It must NOT be the give-up message.
    assert "No information could be gathered" not in report


def test_synthesis_failure_keeps_previous_report():
    """If synthesis raises, the previous report is preserved (not blanked) so the
    findings survive the round and the fallback can use them."""
    r = _researcher()

    async def _boom(messages, **kwargs):
        raise RuntimeError("502 after 3 attempts")

    r._llm = _boom
    r._emit = lambda **k: None

    prev = "existing report body"
    out = asyncio.run(r._synthesize("q", _FINDINGS, prev))
    assert out == prev  # unchanged, not emptied
