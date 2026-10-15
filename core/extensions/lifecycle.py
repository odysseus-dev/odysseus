"""lifecycle.py — extension lifecycle hooks.

Extensions declare their behaviour via four named hooks:
  - ``on_install``:    one-time setup (data dir, settings defaults)
  - ``on_enable``:     register routes, tools, MCP, UI, webhooks, events
  - ``on_disable``:    custom teardown (host auto-undoes registrations)
  - ``on_uninstall``:  custom cleanup (host drops tables after this hook)

This module provides :class:`LifecycleHooks` — a tiny registry that the
extension's ``register(host)`` function populates via ``host.on_install(fn)``
and friends. The host then drives these in order via ``run_install(...)``,
``run_enable(...)``, etc.

Crash isolation: a failing hook is caught and logged to the audit log;
other hooks continue to run. We do NOT propagate hook failures to the
caller because the running server should keep going even if a poorly
written extension throws.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

from .audit import audit_log

logger = logging.getLogger(__name__)


# ── Decorators ─────────────────────────────────────────────────────────────


def on_install(func: Callable) -> Callable:
    """Mark ``func`` as the ``on_install`` hook for an extension.

    Usage inside an extension's ``register(host)``:

        @host.on_install
        def _install():
            host.get_settings().setdefault("token", secrets.token_urlsafe(32))
    """
    func.__oxs_hook__ = "on_install"
    return func


def on_enable(func: Callable) -> Callable:
    func.__oxs_hook__ = "on_enable"
    return func


def on_disable(func: Callable) -> Callable:
    func.__oxs_hook__ = "on_disable"
    return func


def on_uninstall(func: Callable) -> Callable:
    func.__oxs_hook__ = "on_uninstall"
    return func


# Reverse map from hook name to the marker function
_HOOK_MARKERS = {
    "on_install": on_install,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_uninstall": on_uninstall,
}


# ── LifecycleHooks container ───────────────────────────────────────────────


class LifecycleHooks:
    """Collects and runs hooks for one extension instance.

    The :class:`OXSHost` keeps one of these per extension and exposes the
    :func:`on_install`, :func:`on_enable`, :func:`on_disable`, and
    :func:`on_uninstall` decorators as instance methods that append to
    the matching list.

    Hooks are run in registration order. We do not stop on the first error;
    every hook gets a try/except so one bad hook cannot break another.
    """

    def __init__(self, extension_id: str):
        self.extension_id = extension_id
        self._hooks: dict[str, list[Callable]] = {
            "on_install":   [],
            "on_enable":    [],
            "on_disable":   [],
            "on_uninstall": [],
        }

    # ── Registration (called by host.on_install(...), etc.) ─────────────

    def add(self, name: str, func: Callable) -> None:
        if name not in self._hooks:
            raise ValueError(f"unknown hook name: {name!r}")
        if not callable(func):
            raise TypeError(f"hook {name!r} must be callable, got {type(func).__name__}")
        self._hooks[name].append(func)

    def has(self, name: str) -> bool:
        return bool(self._hooks.get(name))

    # ── Execution (called by the registry during enable/disable cycle) ──

    def run(self, name: str, *, ctx: dict | None = None) -> None:
        """Run every registered hook for ``name``. Errors are caught."""
        for fn in self._hooks.get(name, []):
            try:
                if ctx is None:
                    fn()
                else:
                    fn(ctx)
            except Exception:
                logger.exception(
                    "OXS hook '%s' for extension '%s' failed",
                    name, self.extension_id,
                )
                audit_log(
                    f"extension.hook.failed",
                    extension_id=self.extension_id,
                    actor="system",
                    payload={"hook": name, "function": getattr(fn, "__name__", repr(fn))},
                )

    def clear(self) -> None:
        """Drop all hooks (used on disable/uninstall)."""
        for k in self._hooks:
            self._hooks[k] = []


def detect_marker(func: Callable) -> str | None:
    """Return the hook name if ``func`` was decorated with @on_*; else None."""
    return getattr(func, "__oxs_hook__", None)


def install_via_decorators(host: Any, hooks_obj: LifecycleHooks, fn: Callable) -> None:
    """If ``fn`` was decorated with @on_install/@on_enable/etc., register it."""
    name = detect_marker(fn)
    if name is not None:
        hooks_obj.add(name, fn)


__all__ = [
    "on_install",
    "on_enable",
    "on_disable",
    "on_uninstall",
    "LifecycleHooks",
    "detect_marker",
    "install_via_decorators",
]