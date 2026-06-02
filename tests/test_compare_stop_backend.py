"""Regression guard for issue #1508 — Stop in Compare only closed the client SSE
while the model kept generating tokens server-side (LM Studio etc.).

Compare runs are detached on the backend, so aborting the fetch (`AbortController`)
doesn't cancel them — the main chat Stop button POSTs `/api/chat/stop/<sid>` to do
that. The compare stop handlers now do the same per pane.

compare/panes.js pulls in browser globals so it can't run under node; guard the
wiring at the source level.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/compare/panes.js"


def test_compare_stop_cancels_backend_run():
    text = SRC.read_text(encoding="utf-8")
    # A helper that hits the backend stop endpoint for a pane's session.
    assert "/api/chat/stop/" in text, "compare stop must POST the backend stop endpoint (#1508)"
    assert re.search(r"function _backendStopPane\(", text)
    # Both stop paths must invoke it (per-pane and stop-all).
    assert text.count("_backendStopPane(") >= 3  # def + stopPane + stopAll
