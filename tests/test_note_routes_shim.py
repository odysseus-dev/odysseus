"""Regression test for the note route shim (slice 2f, #4082/#4071).

The backward-compat shim at ``routes/note_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.note.*``
path resolve to the *same* module object. This is required because
``test_note_reminder_fire_scope.py`` and ``test_notes_fail_closed_auth.py``
do ``import routes.note_routes as note_routes`` followed by
``monkeypatch.setattr(note_routes, "SessionLocal", ...)`` — for those patches
to take effect at runtime, the legacy module object and the canonical one
must be identical. This test pins that contract.
"""

import importlib

import routes.note_routes as _shim_note  # noqa: F401


def test_legacy_and_canonical_note_module_are_same_object():
    """``import routes.note_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.note_routes")
    canonical = importlib.import_module("routes.note.note_routes")
    assert legacy is canonical, (
        "routes.note_routes shim must resolve to the canonical "
        "routes.note.note_routes module object"
    )


def test_monkeypatch_via_legacy_alias_reaches_canonical(monkeypatch):
    """Patching through the legacy alias must reach the canonical module.

    Several note tests do ``import routes.note_routes as note_routes``
    followed by ``monkeypatch.setattr(note_routes, "SessionLocal", ...)``.
    For that to take effect at runtime, the legacy module object and the
    canonical one must be identical.
    """
    legacy = importlib.import_module("routes.note_routes")
    canonical = importlib.import_module("routes.note.note_routes")

    sentinel = object()
    monkeypatch.setattr(legacy, "setup_note_routes", sentinel)
    assert canonical.setup_note_routes is sentinel, (
        "monkeypatch via legacy alias did not reach the canonical module"
    )
