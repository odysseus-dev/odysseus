"""Shared helper for saving and restoring Python import state in tests.

Use ``preserve_import_state`` as a context manager around any block that needs
to mutate ``sys.modules`` or parent-package attributes temporarily. On exit
(normal or exception), every named module is restored to exactly the state it
had before the block — present, absent, or carrying a parent-package attribute.

Use ``clear_module`` to drop a single module from both ``sys.modules`` and its
parent-package attribute (e.g. before forcing a fresh import inside the block).

Use ``clear_fake_database_modules`` to evict a *stubbed* ``core.database`` (and
its companion ``src.database``) that another test left in import state, without
touching a real ``core.database`` loaded from disk.

Use ``clear_fake_endpoint_resolver_modules`` to evict a *stubbed*
``src.endpoint_resolver`` (and the route modules that imported it) that another
test left in import state, without touching a real ``src.endpoint_resolver``
loaded from disk.

Background: importing ``routes.session_routes`` also sets ``session_routes`` on
the parent ``routes`` package object. A ``from routes import session_routes``
or ``import routes.session_routes as X`` statement resolves through that parent
attribute, so restoring ``sys.modules`` alone is not sufficient — the parent
attribute must be restored too. This helper handles both.

Restoration in ``preserve_import_state`` is two-phased: all ``sys.modules``
entries are written back first, then all parent-package attributes. This means
parent-attr restoration always resolves the parent through the already-restored
``sys.modules``, so results are deterministic regardless of argument order —
safe for callers that pass both a parent package and a child module.
"""

import sys
from contextlib import contextmanager

_ABSENT = object()


def _save_one(dotted_name):
    saved_mod = sys.modules.get(dotted_name, _ABSENT)
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    saved_attr = getattr(pkg, attr, _ABSENT) if pkg is not None else _ABSENT
    return saved_mod, saved_attr


def _restore_parent_attr(dotted_name, saved_attr):
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        return
    if saved_attr is _ABSENT:
        if hasattr(pkg, attr):
            delattr(pkg, attr)
    else:
        setattr(pkg, attr, saved_attr)


def _restore_one(dotted_name, saved_mod, saved_attr):
    if saved_mod is _ABSENT:
        sys.modules.pop(dotted_name, None)
    else:
        sys.modules[dotted_name] = saved_mod
    _restore_parent_attr(dotted_name, saved_attr)


def clear_module(dotted_name):
    """Remove a module from sys.modules and its parent-package attribute."""
    _restore_one(dotted_name, _ABSENT, _ABSENT)


def clear_fake_database_modules():
    """Evict a *stubbed* ``core.database`` (and ``src.database``) from import state.

    Test-only. Some tests install a fake ``core.database`` — a stub module with
    no on-disk ``__file__`` — into ``sys.modules`` and onto the ``core`` package.
    A later test that needs the real database module must evict that stub first,
    or its ``import core.database`` resolves to the fake.

    This is deliberately conservative and mirrors the per-file helpers it
    replaces:

    * It acts only when ``core.database`` is a fake/stub, detected by a missing
      string ``__file__``. A real ``core.database`` loaded from disk is left
      untouched, as is the case where nothing is cached.
    * When it does act, it also drops the cached ``src.database`` entry.
    * It removes the ``core.database`` parent-package attribute only when that
      attribute is the same fake object being evicted.
    """
    parent = sys.modules.get("core")
    attr = getattr(parent, "database", None) if parent is not None else None
    mod = sys.modules.get("core.database") or attr
    if mod is None or isinstance(getattr(mod, "__file__", None), str):
        return
    sys.modules.pop("core.database", None)
    sys.modules.pop("src.database", None)
    if parent is not None and attr is mod:
        delattr(parent, "database")


def clear_fake_endpoint_resolver_modules(*extra_modules):
    """Evict a *stubbed* ``src.endpoint_resolver`` (and dependent route modules).

    Test-only. Several route tests need the *real* ``src.endpoint_resolver`` URL
    helpers, but another test may have installed a fake — a stub module with no
    on-disk ``__file__`` — into ``sys.modules`` and onto the ``src`` package
    during collection. The route modules (``routes.model_routes`` and any extras
    passed in, e.g. ``routes.chat_routes``) get cached against that fake on first
    import, so they must be evicted too.

    Conservative, mirroring ``clear_fake_database_modules`` and the per-file
    guards it replaces:

    * It acts only when ``src.endpoint_resolver`` is a fake/stub, detected by a
      falsy ``__file__`` (missing, ``None``, or empty string) — exactly the
      truthiness check the old inline guards used. A real resolver loaded from
      disk carries a truthy ``__file__`` and is left untouched, as is the case
      where nothing is cached. When the resolver is real, the dependent route
      modules are left untouched too.
    * When it does act, it drops ``routes.model_routes`` plus every name in
      ``extra_modules``.
    * It removes the ``src.endpoint_resolver`` parent-package attribute only when
      that attribute is the same fake object being evicted.

    Behavior delta vs. the old bare ``sys.modules.pop(...)`` guards: dependent
    modules are dropped via :func:`clear_module`, which also clears the parent
    ``routes`` package attribute (e.g. ``routes.model_routes``), not just the
    ``sys.modules`` entry. This prevents a stale parent attribute from shadowing
    the fresh import — the same parent-attr handling the rest of this helper
    family already applies.
    """
    parent = sys.modules.get("src")
    attr = getattr(parent, "endpoint_resolver", None) if parent is not None else None
    mod = sys.modules.get("src.endpoint_resolver") or attr
    if mod is None or getattr(mod, "__file__", None):
        return
    sys.modules.pop("src.endpoint_resolver", None)
    if parent is not None and attr is mod:
        delattr(parent, "endpoint_resolver")
    clear_module("routes.model_routes")
    for name in extra_modules:
        clear_module(name)


@contextmanager
def preserve_import_state(*module_names):
    """Save and restore sys.modules entries and parent-package attributes.

    Restoration is two-phased: sys.modules entries are written back first,
    then parent-package attributes. This ensures parent-attr restoration always
    sees the correctly restored parent in sys.modules, regardless of argument
    order — safe for callers that pass both a parent and a child module.

    On exit (normal or exception), each named module is restored to its state
    before the block — whether present, absent, or carrying a parent attribute.
    """
    saved = {name: _save_one(name) for name in module_names}
    try:
        yield
    finally:
        # Phase 1: restore all sys.modules entries.
        for name, (saved_mod, _) in saved.items():
            if saved_mod is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved_mod
        # Phase 2: restore all parent-package attributes.
        for name, (_, saved_attr) in saved.items():
            _restore_parent_attr(name, saved_attr)
