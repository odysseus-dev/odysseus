"""Regression guard for issue #1355 — the Cookbook *download* error toast used
the default ~1.2s duration, so an actionable message like "tmux is required …"
vanished before it could be read. The serve path already used multi-second
durations; the download-failure toasts now match.

cookbookDownload.js pulls in browser globals so it can't run under node; this
guards the durations at the source level.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/cookbookDownload.js"
_MIN_MS = 5000


def test_download_failure_toasts_stay_visible():
    # Each download-failure toast is a single line; assert each carries an
    # explicit duration >= _MIN_MS so the actionable error stays readable.
    lines = [
        ln for ln in SRC.read_text(encoding="utf-8").splitlines()
        if "showToast(" in ln and "Download failed:" in ln
    ]
    assert lines, "expected at least one 'Download failed' showToast call"
    for ln in lines:
        m = re.search(r",\s*(\d{3,})\s*\)\s*;?\s*$", ln)
        assert m, f"download-failure toast has no explicit duration: {ln.strip()}"
        assert int(m.group(1)) >= _MIN_MS, f"duration too short to read: {ln.strip()}"
