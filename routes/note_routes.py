"""Backward-compat shim — canonical location is routes/note/note_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.note_routes``, ``from routes.note_routes import X``,
``importlib.import_module("routes.note_routes")``, and the
``import ... as note_routes`` + ``monkeypatch.setattr(note_routes, "SessionLocal",
...)`` pattern used by test_note_reminder_fire_scope.py /
test_notes_fail_closed_auth.py all operate on the *same* object the
application actually uses. Keeps existing import paths working after
slice 2f (#4082/#4071). Source-introspection tests read the canonical file
by path.
"""

import sys as _sys

from routes.note import note_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
