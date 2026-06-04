"""Regression guard for issue #2478 — Enter must select a slash-command from the menu.

The slash-command autocomplete popup (static/js/slashAutocomplete.js) wires its
keydown handling up through a late async ``import()`` from chat.js, so a
bubble-phase listener on the ``#message`` textarea registers *after* the
composer's own Enter-to-send handler on the same element. Same element + same
phase ⇒ registration order wins ⇒ the composer submits the half-typed ``/command``
text and the menu's selection never runs, so the menu can't be chosen with the
keyboard.

The fix listens on ``document`` in the CAPTURE phase (so it runs before the
composer's bubble handler regardless of registration order), filters to the
composer element, and calls ``stopImmediatePropagation()`` on the keys it
consumes so the submit path does not also fire. A fully-typed exact command
(``exactHit``) is deliberately left to propagate so it still submits.

Kept as a static source check because slashAutocomplete.js is browser-coupled
and not importable in pytest (matches tests/test_document_editor_scroll.py).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AC_JS = (ROOT / "static/js/slashAutocomplete.js").read_text()


def _keydown_listener_block() -> str:
    """The document keydown listener body, sliced from its registration to ``}, true);``."""
    start = AC_JS.index("document.addEventListener('keydown'")
    end = AC_JS.index("}, true);", start) + len("}, true);")
    return AC_JS[start:end]


def test_keydown_is_captured_on_document_not_bubbled_on_textarea():
    block = _keydown_listener_block()
    # Capture phase on an ancestor — wins the Enter race against the composer.
    assert block.endswith("}, true);")
    # The old bubble-phase textarea listener must be gone, or the bug returns.
    assert "textarea.addEventListener('keydown'" not in AC_JS


def test_handler_filters_to_the_composer():
    # Listening on document means the handler sees every keydown; it must only
    # act on the composer textarea.
    assert "if (e.target !== textarea) return;" in _keydown_listener_block()


def test_consumed_keys_stop_propagation_to_suppress_submit():
    block = _keydown_listener_block()
    # ArrowDown / ArrowUp / insert / Escape each suppress the composer's handler.
    assert block.count("e.stopImmediatePropagation();") >= 4


def test_fully_typed_command_still_submits():
    # The exactHit branch must NOT stop propagation — a fully-typed command
    # should still reach the composer's submit path (hide + return, no stop).
    block = _keydown_listener_block()
    exact_idx = block.index("exactHit")
    # Between the exactHit match and its early `return;`, there must be no
    # stopImmediatePropagation (otherwise typed commands would stop submitting).
    return_idx = block.index("return;", exact_idx)
    assert "stopImmediatePropagation" not in block[exact_idx:return_idx]
    assert "let the normal submit path handle it" in block
