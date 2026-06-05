"""Shared helper for saving and restoring Python import state in tests.

Use ``preserve_import_state`` as a context manager around any block that needs
to mutate ``sys.modules`` or parent-package attributes temporarily. On exit
(normal or exception), every named module is restored to exactly the state it
had before the block — present, absent, or carrying a parent-package attribute.

Use ``clear_module`` to drop a single module from both ``sys.modules`` and its
parent-package attribute (e.g. before forcing a fresh import inside the block).

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
