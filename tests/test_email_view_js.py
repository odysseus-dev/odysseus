"""Wiring checks for the agent-driven email view switch (set_email_view).

No JS test harness exists in this repo (see CONTRIBUTING — node --check only),
so we assert the source wires the pieces together. Behavioral coverage of the
action itself lives in tests/test_ui_control_email_view.py.
"""

from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent / "static" / "js"


def test_chatstream_dispatches_set_email_view():
    src = (_STATIC / "chatStream.js").read_text(encoding="utf-8")
    assert "set_email_view" in src
    # Dispatch targets the in-place updater (preserves the user's window/dock),
    # not openEmailLibrary directly.
    assert "setEmailView" in src


def test_emaillibrary_applies_view_opts():
    src = (_STATIC / "emailLibrary.js").read_text(encoding="utf-8")
    assert "state._libFrom" in src                            # from plumbing
    assert "&from=${encodeURIComponent(fromAtStart)}" in src  # loader passes from
    assert "_resolveRequestedFolder" in src                   # alias resolution
    assert "opts.hasAttachments" in src                       # attachments opt applied


def test_emaillibrary_has_inplace_setemailview():
    # set_email_view must update an open panel in place rather than recreating
    # the modal (which would discard the user's window position/dock).
    src = (_STATIC / "emailLibrary.js").read_text(encoding="utf-8")
    assert "export function setEmailView" in src
