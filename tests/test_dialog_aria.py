"""Pin the dialog accessibility semantics added for the roadmap a11y pass.

Screen readers only announce "dialog" (and its name) when the container
carries role="dialog" plus an accessible name. These checks lock that in for
the static modals in index.html and the JS-built confirm/prompt dialogs, and
guard against a close button shipping without an accessible label again.

Plain text/regex assertions (no bs4 dependency), matching the lightweight style
of the other tests in this suite.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_UI = (_REPO / "static" / "js" / "ui.js").read_text(encoding="utf-8")


def test_static_modals_expose_dialog_role_and_name():
    # Each static tool window must announce itself as a named dialog. These are
    # dockable/tiling windows, so they are role="dialog" WITHOUT aria-modal.
    for name in ("Brain", "Theme", "Prompt", "Rename session", "Cookbook", "Settings"):
        assert f'role="dialog" aria-label="{name}"' in _INDEX, f"missing dialog role/name for {name!r}"


def test_no_modal_close_button_is_unlabeled():
    # Every .close-btn must carry an accessible name (text glyph alone reads as
    # "heavy multiplication x"). Catch any new close button that forgets one.
    buttons = re.findall(r'<button[^>]*class="close-btn"[^>]*>', _INDEX)
    assert buttons, "expected to find close-btn buttons in index.html"
    unlabeled = [b for b in buttons if "aria-label=" not in b]
    assert not unlabeled, f"close buttons missing aria-label: {unlabeled}"


def test_styled_confirm_and_prompt_are_modal_dialogs():
    # The JS-built confirm/prompt overlays ARE blocking modals, so they get
    # role="dialog" + aria-modal="true" and are labelled by their title.
    assert 'class="modal-content styled-confirm-box" role="dialog" aria-modal="true"' in _UI
    assert 'aria-labelledby="styled-confirm-title"' in _UI
    assert '<h4 id="styled-confirm-title">Confirm</h4>' in _UI

    assert 'styled-prompt-box" role="dialog" aria-modal="true"' in _UI
    assert 'aria-labelledby="styled-prompt-title"' in _UI
    # The label/description targets the styled-prompt dialog points at must exist.
    assert 'id="styled-prompt-title"' in _UI
    assert 'id="styled-prompt-msg"' in _UI


def test_styled_dialogs_manage_focus():
    # A dialog is only really accessible if it restores focus to the trigger on
    # close and traps Tab while open. Both styledConfirm and styledPrompt should
    # capture the previously-focused element, restore it, and trap Tab.
    assert _UI.count("const _prevFocus = document.activeElement;") == 2
    assert _UI.count("_prevFocus && _prevFocus.focus && _prevFocus.focus()") == 2
    assert _UI.count("e.key === 'Tab'") == 2

