"""Guard that toast dismissal (via the × close button) correctly resets
pointer-events so the invisible fixed overlay does not block clicks.

The reviewer flagged that action-toasts set ``pointer-events: auto`` on
``#toast`` for their clickable button, but the close-button dismiss path
was cancelling the auto-hide timer without resetting ``pointer-events``.
This left an invisible element intercepting mouse/touch events.

These are source-level assertions (no browser, no DOM) that verify every
dismissal path delegates to the shared lifecycle helper and that the helper
performs the reset.  They cover:
  • ordinary (plain text) toast  – showToast
  • error toast                  – showError
  • action toast                 – showToast with action opts
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_UI_PATH = _REPO / "static" / "js" / "ui.js"


def _read_ui():
    return _UI_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers – extract the close-button event-handler bodies from each function.
# ---------------------------------------------------------------------------

def _extract_function(src: str, func_name: str) -> str:
    """Return the full body of *func_name* (exported or not)."""
    # Match   export function showToast(…  or  function showToast(…
    pat = re.compile(
        rf"(?:export\s+)?function\s+{re.escape(func_name)}\s*\(", re.DOTALL
    )
    m = pat.search(src)
    assert m, f"could not find function {func_name!r} in ui.js"
    start = m.start()
    # Walk forward counting braces to find the matching closing brace.
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces for {func_name}")


def _extract_close_handler(func_body: str) -> str:
    """Return the close-button click-handler body inside *func_body*.

    Looks for the ``closeBtn`` declaration, then finds the
    ``addEventListener('click'`` call that follows, and extracts the arrow
    function body.
    """
    idx = func_body.find("const closeBtn")
    assert idx != -1, "closeBtn not found in function body"
    # Find the addEventListener('click', … that follows
    listen_idx = func_body.find("addEventListener('click'", idx)
    if listen_idx == -1:
        listen_idx = func_body.find('addEventListener("click"', idx)
    assert listen_idx != -1, "addEventListener('click') not found after toast-close-btn"

    # Find the opening brace of the handler
    brace = func_body.find("{", listen_idx)
    assert brace != -1
    depth = 0
    for i in range(brace, len(func_body)):
        if func_body[i] == "{":
            depth += 1
        elif func_body[i] == "}":
            depth -= 1
            if depth == 0:
                return func_body[brace : i + 1]
    raise AssertionError("unbalanced braces in close handler")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_finish_toast_resets_pointer_events():
    """The shared lifecycle helper must always restore non-blocking hit testing."""
    src = _read_ui()
    body = _extract_function(src, "_finishToast")
    assert "pointerEvents = ''" in body or 'pointerEvents = ""' in body, (
        "_finishToast does not reset pointerEvents – dismissed action toasts "
        "will leave an invisible click-blocking overlay"
    )


def test_showToast_close_handler_finishes_toast():
    """showToast's × handler must use the lifecycle helper that resets hit testing."""
    src = _read_ui()
    body = _extract_function(src, "showToast")
    handler = _extract_close_handler(body)
    assert "_finishToast(toastEl)" in handler, (
        "showToast close-button handler does not run the shared toast cleanup"
    )


def test_showError_clears_pointer_events_and_finishes_on_timeout():
    """Error toasts must clear stale hit testing and use shared timer cleanup."""
    src = _read_ui()
    body = _extract_function(src, "showError")
    assert "pointerEvents = ''" in body or 'pointerEvents = ""' in body, (
        "showError does not clear pointerEvents left by an action toast"
    )
    assert "_finishToast(toastEl)" in body, (
        "showError auto-hide timer does not run the shared toast cleanup"
    )


def test_showToast_timer_finishes_toast():
    """The auto-hide timer must use the lifecycle helper that resets hit testing."""
    src = _read_ui()
    body = _extract_function(src, "showToast")
    # The _hideTimer setTimeout body should contain the reset
    timer_idx = body.find("_hideTimer")
    assert timer_idx != -1, "no _hideTimer found in showToast"
    # Find the setTimeout callback after the last _hideTimer assignment
    last_timer = body.rfind("_hideTimer = setTimeout")
    assert last_timer != -1
    # Extract the setTimeout callback body
    brace = body.find("{", last_timer)
    depth = 0
    timer_body = ""
    for i in range(brace, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                timer_body = body[brace : i + 1]
                break
    assert "_finishToast(toastEl)" in timer_body, (
        "showToast auto-hide timer does not run the shared toast cleanup"
    )


def test_action_toast_sets_pointer_events_auto():
    """When an action button is present the toast must set pointer-events
    to 'auto' so the button is clickable."""
    src = _read_ui()
    body = _extract_function(src, "showToast")
    assert "pointerEvents = 'auto'" in body or 'pointerEvents = "auto"' in body, (
        "showToast no longer sets pointer-events:auto for action toasts"
    )


def test_plain_toast_clears_pointer_events():
    """When there is NO action button, showToast must clear any leftover
    pointer-events from a previous action toast."""
    src = _read_ui()
    body = _extract_function(src, "showToast")
    # The else-branch of the action check should reset pointerEvents
    assert "pointerEvents = ''" in body or 'pointerEvents = ""' in body, (
        "showToast does not clear pointer-events for non-action toasts"
    )
