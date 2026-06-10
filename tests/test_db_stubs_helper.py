import sys
from contextlib import contextmanager
from types import ModuleType
from unittest.mock import MagicMock

from pytest import MonkeyPatch

from tests.helpers.db_stubs import make_core_db_stub


_MISSING = object()
_MODULE_NAMES = ("core", "core.database")


@contextmanager
def _preserve_core_modules():
    original_modules = {
        name: sys.modules.get(name, _MISSING) for name in _MODULE_NAMES
    }
    try:
        yield
    finally:
        for name in _MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, module in original_modules.items():
            if module is not _MISSING:
                sys.modules[name] = module


def test_models_create_mock_attributes(monkeypatch):
    db = make_core_db_stub(monkeypatch, models=("User", "Session"))

    assert sys.modules["core.database"] is db
    assert isinstance(db.SessionLocal, MagicMock)
    assert isinstance(db.User, MagicMock)
    assert isinstance(db.Session, MagicMock)


def test_attributes_override_defaults_and_model_mocks(monkeypatch):
    session_local = object()
    email_account = object()

    db = make_core_db_stub(
        monkeypatch,
        models=("EmailAccount",),
        attributes={
            "SessionLocal": session_local,
            "EmailAccount": email_account,
        },
    )

    assert db.SessionLocal is session_local
    assert db.EmailAccount is email_account


def test_core_module_installation_is_opt_in():
    with _preserve_core_modules():
        sys.modules.pop("core", None)
        sys.modules.pop("core.database", None)
        monkeypatch = MonkeyPatch()
        try:
            db = make_core_db_stub(monkeypatch)

            assert "core" not in sys.modules
            assert sys.modules["core.database"] is db
        finally:
            monkeypatch.undo()


def test_existing_core_is_preserved_when_installation_is_disabled():
    with _preserve_core_modules():
        original_core = ModuleType("core")
        sys.modules["core"] = original_core
        sys.modules.pop("core.database", None)
        monkeypatch = MonkeyPatch()
        try:
            db = make_core_db_stub(monkeypatch, install_core_package=False)

            assert sys.modules["core"] is original_core
            assert sys.modules["core.database"] is db
        finally:
            monkeypatch.undo()

        assert sys.modules["core"] is original_core
        assert "core.database" not in sys.modules


def test_undo_removes_modules_that_were_absent():
    with _preserve_core_modules():
        sys.modules.pop("core", None)
        sys.modules.pop("core.database", None)
        monkeypatch = MonkeyPatch()
        try:
            make_core_db_stub(monkeypatch, install_core_package=True)

            assert "core" in sys.modules
            assert "core.database" in sys.modules
        finally:
            monkeypatch.undo()

        assert "core" not in sys.modules
        assert "core.database" not in sys.modules


def test_undo_restores_existing_modules():
    with _preserve_core_modules():
        original_core = ModuleType("core")
        original_database = ModuleType("core.database")
        sys.modules["core"] = original_core
        sys.modules["core.database"] = original_database
        monkeypatch = MonkeyPatch()
        try:
            make_core_db_stub(monkeypatch, install_core_package=True)

            assert sys.modules["core"] is not original_core
            assert sys.modules["core.database"] is not original_database
        finally:
            monkeypatch.undo()

        assert sys.modules["core"] is original_core
        assert sys.modules["core.database"] is original_database
