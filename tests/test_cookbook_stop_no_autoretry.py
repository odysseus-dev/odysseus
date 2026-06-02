"""Regression guard for issue #1458 — Stop / Stop all didn't actually stop a
download because the watcher auto-retried it.

A user stop kills the download process, which prints DOWNLOAD_FAILED — the same
marker a crash/network blip prints. The bounded auto-retry in cookbookRunning.js
couldn't tell them apart, so it relaunched the very download the user just
stopped (and the toast showed "stopped" while it kept going).

The fix tracks user-stopped downloads in `_userStoppedDownloads` and skips
auto-retry for them. cookbookRunning.js pulls in browser globals so it can't run
under node; this guards the wiring at the source level.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/cookbookRunning.js"


def test_user_stopped_downloads_skip_autoretry():
    text = SRC.read_text(encoding="utf-8")
    # The tracking set must exist.
    assert "_userStoppedDownloads" in text
    # The Stop handler must record the download as user-stopped.
    assert re.search(r"_userStoppedDownloads\.add\(", text), "Stop must mark the download"
    # The auto-retry condition must skip user-stopped downloads.
    assert re.search(r"!_userStoppedDownloads\.has\(_dlKey\)", text), \
        "auto-retry must skip user-stopped downloads"
    # A fresh download / deliberate retry must clear the mark so genuine
    # interruptions can still auto-retry.
    assert text.count("_userStoppedDownloads.delete(") >= 1
