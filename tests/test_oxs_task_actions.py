"""Tests for OXS task actions."""

from __future__ import annotations

import pytest

from core.extensions import task_actions


@pytest.fixture(autouse=True)
def _clean_both_dicts():
    """Remove any OXS-namespaced keys from BUILTIN_ACTIONS / INFO before each test."""
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    before_actions = set(BUILTIN_ACTIONS)
    before_info = set(BUILTIN_ACTION_INFO)
    yield
    for k in list(BUILTIN_ACTIONS):
        if k.startswith("oxs_"):
            del BUILTIN_ACTIONS[k]
    for k in list(BUILTIN_ACTION_INFO):
        if k.startswith("oxs_"):
            del BUILTIN_ACTION_INFO[k]


def test_install_and_list_in_storage():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO

    async def my_action(owner, **kwargs):
        return ("did it", True)

    key = task_actions.install_action("ext1", "classify", my_action, {"type": "object"})
    assert key == "oxs_ext1__classify"
    assert key in BUILTIN_ACTIONS
    assert key in BUILTIN_ACTION_INFO
    assert BUILTIN_ACTION_INFO[key]["source"] == "extension"
    assert BUILTIN_ACTION_INFO[key]["extension_id"] == "ext1"
    assert list(task_actions.list_actions_for("ext1")) == ["classify"]


def test_namespacing_prevents_collision():
    from src.builtin_actions import BUILTIN_ACTIONS

    async def a(owner, **kw): return ("a", True)
    async def b(owner, **kw): return ("b", True)

    k1 = task_actions.install_action("ext1", "classify", a, {})
    k2 = task_actions.install_action("ext2", "classify", b, {})
    assert k1 != k2
    assert BUILTIN_ACTIONS[k1] is not BUILTIN_ACTIONS[k2]


def test_uninstall_removes_action():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO

    async def fn(owner, **kw): return ("x", True)
    key = task_actions.install_action("ext", "do_thing", fn, {})
    assert key in BUILTIN_ACTIONS
    task_actions.uninstall_action("ext", "do_thing")
    assert key not in BUILTIN_ACTIONS
    assert key not in BUILTIN_ACTION_INFO


def test_uninstall_unknown_is_noop():
    task_actions.uninstall_action("nope", "nope")  # does not raise


def test_uninstall_all_actions():
    from src.builtin_actions import BUILTIN_ACTIONS

    async def fn(owner, **kw): return ("", True)
    task_actions.install_action("ext", "a", fn, {})
    task_actions.install_action("ext", "b", fn, {})
    task_actions.install_action("ext", "c", fn, {})
    n = task_actions.uninstall_all_actions("ext")
    assert n == 3
    assert "oxs_ext__a" not in BUILTIN_ACTIONS
    assert "oxs_ext__b" not in BUILTIN_ACTIONS
    assert "oxs_ext__c" not in BUILTIN_ACTIONS


def test_uninstall_all_actions_leaves_others_alone():
    from src.builtin_actions import BUILTIN_ACTIONS

    async def fn(owner, **kw): return ("", True)
    task_actions.install_action("ext1", "a", fn, {})
    task_actions.install_action("ext2", "b", fn, {})
    n = task_actions.uninstall_all_actions("ext1")
    assert n == 1
    assert "oxs_ext1__a" not in BUILTIN_ACTIONS
    assert "oxs_ext2__b" in BUILTIN_ACTIONS


def test_list_actions_for_only_returns_own():
    async def fn(owner, **kw): return ("", True)
    task_actions.install_action("ext1", "a", fn, {})
    task_actions.install_action("ext1", "b", fn, {})
    task_actions.install_action("ext2", "c", fn, {})
    assert task_actions.list_actions_for("ext1") == ["a", "b"]
    assert task_actions.list_actions_for("ext2") == ["c"]


def test_wrapper_returns_tuple_unpacked():
    """The wrapper must adapt a 2-tuple result to scheduler contract."""
    import asyncio
    from src.builtin_actions import BUILTIN_ACTIONS

    async def fn(owner, **kw):
        return ("result-text", True)
    key = task_actions.install_action("ext", "a", fn, {})
    adapter = BUILTIN_ACTIONS[key]
    text, success = asyncio.run(adapter(owner="admin"))
    assert text == "result-text"
    assert success is True


def test_wrapper_adapts_truthy_return():
    """Handlers that return just True are accepted."""
    import asyncio
    from src.builtin_actions import BUILTIN_ACTIONS

    async def fn(owner, **kw):
        return True
    key = task_actions.install_action("ext", "a", fn, {})
    text, success = asyncio.run(BUILTIN_ACTIONS[key](owner="admin"))
    assert success is True


def test_wrapper_handles_exception():
    """A throwing handler must not crash the scheduler."""
    import asyncio
    from src.builtin_actions import BUILTIN_ACTIONS

    async def fn(owner, **kw):
        raise RuntimeError("boom")
    key = task_actions.install_action("ext", "a", fn, {})
    text, success = asyncio.run(BUILTIN_ACTIONS[key](owner="admin"))
    assert success is False