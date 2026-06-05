"""Focused tests for tests/helpers/import_state.py."""
import sys
import types

import pytest

from tests.helpers.import_state import (
    clear_fake_database_modules,
    clear_module,
    preserve_import_state,
)

_SENTINEL = "tests._import_state_test_sentinel"

# Names touched by clear_fake_database_modules — snapshot/restore these so the
# tests never leak into the real core/src packages.
_DB_NAMES = ("core", "core.database", "src", "src.database")


def test_absent_module_is_removed_after_block():
    assert _SENTINEL not in sys.modules
    with preserve_import_state(_SENTINEL):
        sys.modules[_SENTINEL] = types.ModuleType(_SENTINEL)
    assert _SENTINEL not in sys.modules


def test_present_module_is_restored_after_block():
    original = types.ModuleType(_SENTINEL)
    sys.modules[_SENTINEL] = original
    try:
        with preserve_import_state(_SENTINEL):
            sys.modules[_SENTINEL] = types.ModuleType(_SENTINEL)
        assert sys.modules[_SENTINEL] is original
    finally:
        sys.modules.pop(_SENTINEL, None)


def test_parent_attr_restored_when_present_before_block():
    fake_parent = types.ModuleType("_fake_istate_parent")
    fake_child = types.ModuleType("_fake_istate_parent.child")
    fake_parent.child = fake_child
    sys.modules["_fake_istate_parent"] = fake_parent
    sys.modules["_fake_istate_parent.child"] = fake_child
    try:
        with preserve_import_state("_fake_istate_parent.child"):
            replacement = types.ModuleType("_fake_istate_parent.child")
            sys.modules["_fake_istate_parent.child"] = replacement
            fake_parent.child = replacement
        assert sys.modules["_fake_istate_parent.child"] is fake_child
        assert fake_parent.child is fake_child
    finally:
        sys.modules.pop("_fake_istate_parent", None)
        sys.modules.pop("_fake_istate_parent.child", None)


def test_parent_attr_removed_when_absent_before_block():
    fake_parent = types.ModuleType("_fake_istate_parent")
    sys.modules["_fake_istate_parent"] = fake_parent
    try:
        with preserve_import_state("_fake_istate_parent.child"):
            fake_child = types.ModuleType("_fake_istate_parent.child")
            sys.modules["_fake_istate_parent.child"] = fake_child
            fake_parent.child = fake_child
        assert "_fake_istate_parent.child" not in sys.modules
        assert not hasattr(fake_parent, "child")
    finally:
        sys.modules.pop("_fake_istate_parent", None)
        sys.modules.pop("_fake_istate_parent.child", None)


def test_state_restored_on_exception():
    assert _SENTINEL not in sys.modules
    with pytest.raises(RuntimeError, match="expected"):
        with preserve_import_state(_SENTINEL):
            sys.modules[_SENTINEL] = types.ModuleType(_SENTINEL)
            raise RuntimeError("expected")
    assert _SENTINEL not in sys.modules


def test_multiple_modules_all_restored():
    names = [f"tests._istate_multi_{i}" for i in range(3)]
    for n in names:
        assert n not in sys.modules
    with preserve_import_state(*names):
        for n in names:
            sys.modules[n] = types.ModuleType(n)
    for n in names:
        assert n not in sys.modules


def test_clear_module_removes_entry():
    sys.modules[_SENTINEL] = types.ModuleType(_SENTINEL)
    try:
        clear_module(_SENTINEL)
        assert _SENTINEL not in sys.modules
    finally:
        sys.modules.pop(_SENTINEL, None)


def test_clear_module_removes_parent_attr():
    fake_parent = types.ModuleType("_fake_istate_parent")
    fake_child = types.ModuleType("_fake_istate_parent.child")
    fake_parent.child = fake_child
    sys.modules["_fake_istate_parent"] = fake_parent
    sys.modules["_fake_istate_parent.child"] = fake_child
    try:
        clear_module("_fake_istate_parent.child")
        assert "_fake_istate_parent.child" not in sys.modules
        assert not hasattr(fake_parent, "child")
    finally:
        sys.modules.pop("_fake_istate_parent", None)
        sys.modules.pop("_fake_istate_parent.child", None)


def test_clear_module_tolerates_absent_entry():
    assert _SENTINEL not in sys.modules
    clear_module(_SENTINEL)  # must not raise


def test_parent_attr_restored_correctly_when_parent_also_preserved():
    """When a parent package and its child are both named, the child's
    parent-attr restore must target the *saved* parent module, not the mutated
    one. This requires phase 1 (sys.modules) to complete before phase 2 (attrs).
    Tested with child listed before parent to trigger the failure path in a
    naive single-pass implementation.
    """
    fake_parent = types.ModuleType("_fake_istate_parent")
    fake_child = types.ModuleType("_fake_istate_parent.child")
    fake_parent.child = fake_child
    sys.modules["_fake_istate_parent"] = fake_parent
    sys.modules["_fake_istate_parent.child"] = fake_child
    try:
        # child before parent: old single-pass restore would write the child attr
        # onto the still-mutated parent, then replace sys.modules["_fake_istate_parent"]
        # — leaving fake_parent.child untouched.
        with preserve_import_state("_fake_istate_parent.child", "_fake_istate_parent"):
            new_parent = types.ModuleType("_fake_istate_parent")
            new_child = types.ModuleType("_fake_istate_parent.child")
            new_parent.child = new_child
            sys.modules["_fake_istate_parent"] = new_parent
            sys.modules["_fake_istate_parent.child"] = new_child
        # sys.modules entries restored
        assert sys.modules["_fake_istate_parent"] is fake_parent
        assert sys.modules["_fake_istate_parent.child"] is fake_child
        # parent-attr written onto the restored (saved) parent, not the mutated one
        assert fake_parent.child is fake_child
    finally:
        sys.modules.pop("_fake_istate_parent", None)
        sys.modules.pop("_fake_istate_parent.child", None)


def test_clear_fake_database_removes_stub_core_database():
    with preserve_import_state(*_DB_NAMES):
        fake_core = types.ModuleType("core")
        fake_db = types.ModuleType("core.database")  # no __file__ => a stub
        fake_core.database = fake_db
        sys.modules["core"] = fake_core
        sys.modules["core.database"] = fake_db

        clear_fake_database_modules()

        assert "core.database" not in sys.modules
        assert not hasattr(fake_core, "database")


def test_clear_fake_database_preserves_real_core_database():
    with preserve_import_state(*_DB_NAMES):
        fake_core = types.ModuleType("core")
        real_db = types.ModuleType("core.database")
        real_db.__file__ = "/somewhere/core/database.py"  # looks on-disk
        fake_core.database = real_db
        sys.modules["core"] = fake_core
        sys.modules["core.database"] = real_db

        clear_fake_database_modules()

        assert sys.modules["core.database"] is real_db
        assert fake_core.database is real_db


def test_clear_fake_database_drops_src_database_when_core_is_fake():
    with preserve_import_state(*_DB_NAMES):
        fake_core = types.ModuleType("core")
        fake_db = types.ModuleType("core.database")
        fake_core.database = fake_db
        sys.modules["core"] = fake_core
        sys.modules["core.database"] = fake_db
        sys.modules["src.database"] = types.ModuleType("src.database")

        clear_fake_database_modules()

        assert "src.database" not in sys.modules


def test_clear_fake_database_leaves_src_database_when_core_is_real():
    with preserve_import_state(*_DB_NAMES):
        fake_core = types.ModuleType("core")
        real_db = types.ModuleType("core.database")
        real_db.__file__ = "/somewhere/core/database.py"
        fake_core.database = real_db
        sys.modules["core"] = fake_core
        sys.modules["core.database"] = real_db
        src_db = types.ModuleType("src.database")
        sys.modules["src.database"] = src_db

        clear_fake_database_modules()

        assert sys.modules["src.database"] is src_db


def test_clear_fake_database_keeps_parent_attr_pointing_elsewhere():
    """When the cached core.database is a stub but the `database` attr on the
    core package points at a *different* object, the attr is left intact —
    only the same fake object is unlinked."""
    with preserve_import_state(*_DB_NAMES):
        fake_core = types.ModuleType("core")
        cached_fake = types.ModuleType("core.database")  # the stub in sys.modules
        other = types.ModuleType("core.database")  # parent attr points here
        fake_core.database = other
        sys.modules["core"] = fake_core
        sys.modules["core.database"] = cached_fake

        clear_fake_database_modules()

        assert "core.database" not in sys.modules
        assert fake_core.database is other


def test_clear_fake_database_uses_parent_attr_when_not_in_sys_modules():
    """A stub reachable only via the core package's `database` attribute (not in
    sys.modules) is still detected and unlinked from the parent."""
    with preserve_import_state(*_DB_NAMES):
        sys.modules.pop("core.database", None)
        fake_core = types.ModuleType("core")
        fake_db = types.ModuleType("core.database")
        fake_core.database = fake_db
        sys.modules["core"] = fake_core

        clear_fake_database_modules()

        assert not hasattr(fake_core, "database")


def test_clear_fake_database_noop_when_nothing_cached():
    with preserve_import_state(*_DB_NAMES):
        sys.modules.pop("core.database", None)
        fake_core = types.ModuleType("core")  # no `database` attr
        sys.modules["core"] = fake_core

        clear_fake_database_modules()  # must not raise

        assert "core.database" not in sys.modules
