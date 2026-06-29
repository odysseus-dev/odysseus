"""Regression test for the memory route shim (slice 2c, #4082/#4071).

The backward-compat shim at ``routes/memory_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.memory.*``
path resolve to the *same* module object. This is required because
``test_memory_routes_session_owner.py`` and ``test_memory_owner_isolation.py``
do ``import routes.memory_routes as mr`` followed by
``monkeypatch.setattr(mr, "get_current_user", ...)`` — for those patches to
take effect at runtime, the legacy module object and the canonical one must
be identical. This test pins that contract.
"""

import importlib

import routes.memory_routes as _shim_memory  # noqa: F401


def test_legacy_and_canonical_memory_module_are_same_object():
    """``import routes.memory_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.memory_routes")
    canonical = importlib.import_module("routes.memory.memory_routes")
    assert legacy is canonical, (
        "routes.memory_routes shim must resolve to the canonical "
        "routes.memory.memory_routes module object"
    )


def test_monkeypatch_via_legacy_alias_reaches_canonical(monkeypatch):
    """Patching through the legacy alias must reach the canonical module.

    Several memory tests do ``import routes.memory_routes as mr`` followed by
    ``monkeypatch.setattr(mr, "get_current_user", ...)``. For that to take
    effect at runtime, the legacy module object and the canonical one must be
    identical.
    """
    legacy = importlib.import_module("routes.memory_routes")
    canonical = importlib.import_module("routes.memory.memory_routes")

    sentinel = object()
    monkeypatch.setattr(legacy, "setup_memory_routes", sentinel)
    assert canonical.setup_memory_routes is sentinel, (
        "monkeypatch via legacy alias did not reach the canonical module"
    )
