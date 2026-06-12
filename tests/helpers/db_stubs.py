"""Shared database stub helpers for CLI and unit tests."""
import sys
import types
from unittest.mock import MagicMock


def make_core_db_stub(
    monkeypatch,
    models=(),
    *,
    attributes=None,
    install_core_package=False,
):
    """Create a core.database stub and inject it via monkeypatch.

    Always sets SessionLocal. Pass model class names via `models` to set
    each as a MagicMock attribute on the stub. Pass `attributes` to override
    specific values, and `install_core_package` when the import also needs a
    stub parent package.

    Returns the stub module for optional further configuration.
    """
    if install_core_package:
        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = []  # Make it a proper package so submodules can be imported
        monkeypatch.setitem(sys.modules, "core", core_pkg)

    db = types.ModuleType("src.infra.database.database")
    db.SessionLocal = MagicMock()
    for name in models:
        setattr(db, name, MagicMock())
    for name, value in (attributes or {}).items():
        setattr(db, name, value)
    monkeypatch.setitem(sys.modules, "src.infra.database.database", db)
    return db
