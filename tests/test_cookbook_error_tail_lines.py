"""Guard for the cookbook error output-tail expansion.

When a task reaches status "error" the status endpoint previously returned
only the last 12 lines of the subprocess log. The "Copy last 50 lines"
context-menu action was therefore copying the same 12 lines — making it
useless for diagnosing failures that emit long stack traces or build output.

This fix:
- Sets _tail_lines = 50 when status == "error", 12 otherwise.
- Initialises exit_code = None before the status-classification block so it
  is always defined in the result dict (was only assigned inside the
  is_alive branch, causing a NameError in the dead-session path).
- Includes exit_code in the task-status response dict.
- The JS poller in cookbookRunning.js captures exit_code from live data so
  it persists in local task state alongside the output.
"""
import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / "routes/cookbook_routes.py"
JS = Path(__file__).resolve().parent.parent / "static/js/cookbookRunning.js"


def test_tail_lines_50_on_error():
    text = ROUTES.read_text(encoding="utf-8")
    # The tail-line count must be determined by a variable, not a raw literal.
    assert "_tail_lines = 50 if status == \"error\" else 12" in text, \
        "expected _tail_lines to be 50 on error, 12 otherwise"
    assert "full_snapshot.splitlines()[-_tail_lines:]" in text, \
        "output_tail must use _tail_lines, not a hardcoded slice"


def test_exit_code_initialised_before_if_block():
    text = ROUTES.read_text(encoding="utf-8")
    # exit_code must be set to None at the same scope level as download_zero_files
    # so that it is always defined when building the result dict, even when the
    # session is already dead (the else-branch that skips the is_alive block).
    assert re.search(
        r"download_zero_files\s*=\s*False\s*\n\s*exit_code\s*=\s*None",
        text,
    ), "exit_code must be initialised to None alongside download_zero_files"


def test_exit_code_in_result_dict():
    text = ROUTES.read_text(encoding="utf-8")
    assert '"exit_code": exit_code' in text, \
        "exit_code must be included in the task-status result dict"


def test_js_poller_captures_exit_code():
    text = JS.read_text(encoding="utf-8")
    assert "live.exit_code != null" in text, \
        "cookbookRunning.js poller must propagate exit_code from live task data"
