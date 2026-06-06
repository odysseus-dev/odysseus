"""Shared database stub helpers for CLI and unit tests."""
import sys
import types
from unittest.mock import MagicMock


def make_core_db_stub(monkeypatch, models=()):
    """Create a core.database stub and inject it via monkeypatch.

    Always sets SessionLocal. Pass model class names via `models` to set
    each as a MagicMock attribute on the stub.

    Returns the stub module for optional further configuration.
    """
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    for name in models:
        setattr(db, name, MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)
    return db
