"""Guard that toast dismissal (via the × close button) correctly resets
pointer-events so the invisible fixed overlay does not block clicks.

The reviewer flagged that action-toasts set ``pointer-events: auto`` on
``#toast`` for their clickable button, but the close-button dismiss path
was cancelling the auto-hide timer without resetting ``pointer-events``.
This left an invisible element intercepting mouse/touch events.

These are source-level assertions (no browser, no DOM) that verify the
close-button handler includes the reset.  They cover:
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

    Looks for the ``toast-close-btn`` class assignment, then finds the
    ``addEventListener('click'`` call that follows, and extracts the arrow
    function body.
    """
    idx = func_body.find("toast-close-btn")
    assert idx != -1, "toast-close-btn not found in function body"
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

def test_showToast_close_handler_resets_pointer_events():
    """showToast's × handler must clear pointer-events so an action-toast
    that set them to 'auto' doesn't leave the overlay blocking clicks."""
    src = _read_ui()
    body = _extract_function(src, "showToast")
    handler = _extract_close_handler(body)
    assert "pointerEvents" in handler, (
        "showToast close-button handler does not reset pointerEvents – "
        "action toasts will leave an invisible click-blocking overlay"
    )


def test_showError_close_handler_resets_pointer_events():
    """showError's × handler must also clear pointer-events defensively,
    in case a prior action-toast left them as 'auto'."""
    src = _read_ui()
    body = _extract_function(src, "showError")
    handler = _extract_close_handler(body)
    assert "pointerEvents" in handler, (
        "showError close-button handler does not reset pointerEvents – "
        "a prior action toast could leave the overlay blocking clicks"
    )


def test_showToast_timer_resets_pointer_events():
    """The auto-hide timer in showToast must also reset pointer-events.
    This was already in place before the × button was added; make sure
    it stays."""
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
    assert "pointerEvents" in timer_body, (
        "showToast auto-hide timer no longer resets pointerEvents"
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
