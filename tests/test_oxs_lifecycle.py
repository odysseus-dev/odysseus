"""Tests for OXS lifecycle hooks."""

from __future__ import annotations

import pytest

from core.extensions import (
    LifecycleHooks,
    on_disable,
    on_enable,
    on_install,
    on_uninstall,
)


# ── Decorators ─────────────────────────────────────────────────────────────


def test_on_install_marks_function():
    @on_install
    def _fn():
        return "x"
    assert _fn.__oxs_hook__ == "on_install"


def test_on_enable_marks_function():
    @on_enable
    def _fn():
        return "x"
    assert _fn.__oxs_hook__ == "on_enable"


def test_on_disable_marks_function():
    @on_disable
    def _fn():
        return "x"
    assert _fn.__oxs_hook__ == "on_disable"


def test_on_uninstall_marks_function():
    @on_uninstall
    def _fn():
        return "x"
    assert _fn.__oxs_hook__ == "on_uninstall"


def test_undecorated_function_has_no_hook_marker():
    def _fn():
        return "x"
    assert not hasattr(_fn, "__oxs_hook__")


# ── LifecycleHooks container ───────────────────────────────────────────────


def test_add_then_has():
    h = LifecycleHooks("test")
    h.add("on_install", lambda: None)
    assert h.has("on_install")
    assert not h.has("on_enable")


def test_add_unknown_hook_raises():
    h = LifecycleHooks("test")
    with pytest.raises(ValueError):
        h.add("not_a_real_hook", lambda: None)


def test_add_non_callable_raises():
    h = LifecycleHooks("test")
    with pytest.raises(TypeError):
        h.add("on_install", "not a function")


def test_run_executes_hooks_in_order():
    h = LifecycleHooks("test")
    calls = []
    h.add("on_enable", lambda: calls.append("a"))
    h.add("on_enable", lambda: calls.append("b"))
    h.add("on_enable", lambda: calls.append("c"))
    h.run("on_enable")
    assert calls == ["a", "b", "c"]


def test_run_passes_context_dict():
    h = LifecycleHooks("test")
    received = []
    def _hook(ctx):
        received.append(ctx)
    h.add("on_install", _hook)
    h.run("on_install", ctx={"k": "v"})
    assert received == [{"k": "v"}]


def test_run_no_context_dict_calls_with_no_args():
    h = LifecycleHooks("test")
    received = []
    def _hook():
        received.append("called")
    h.add("on_install", _hook)
    h.run("on_install")
    assert received == ["called"]


def test_run_isolates_failing_hooks():
    """One failing hook does not stop subsequent hooks from running."""
    h = LifecycleHooks("test")
    calls = []
    def boom():
        raise RuntimeError("boom")
    h.add("on_install", lambda: calls.append("a"))
    h.add("on_install", boom)
    h.add("on_install", lambda: calls.append("b"))
    h.run("on_install")
    assert calls == ["a", "b"]


def test_run_on_unknown_hook_is_noop():
    """Run on a hook name we never registered — no error, no action."""
    h = LifecycleHooks("test")
    h.run("on_enable")  # nothing registered, must not raise


def test_clear_removes_all_hooks():
    h = LifecycleHooks("test")
    h.add("on_install", lambda: None)
    h.add("on_enable", lambda: None)
    h.clear()
    assert not h.has("on_install")
    assert not h.has("on_enable")


def test_run_audits_failing_hook(tmp_path, monkeypatch):
    """A failing hook must write an audit entry."""
    import core.extensions.audit as audit_mod
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit_mod, "OXS_AUDIT_LOG", str(log_path))
    monkeypatch.setattr(audit_mod, "_DEFAULT_SINK",
                        audit_mod.AuditSink(log_path=str(log_path),
                                            db_session_factory=None))
    h = LifecycleHooks("audit-test")

    def boom():
        raise RuntimeError("kaboom")
    h.add("on_enable", boom)
    h.run("on_enable")

    events = audit_mod.audit_tail(10)
    failed = [e for e in events if e["event"] == "extension.hook.failed"]
    assert len(failed) == 1
    assert failed[0]["extension_id"] == "audit-test"
    assert failed[0]["payload"]["hook"] == "on_enable"