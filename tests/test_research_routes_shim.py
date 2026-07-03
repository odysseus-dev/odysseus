"""Regression test for the research route shim (slice 2b, #4082/#4071).

The backward-compat shim at ``routes/research_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.research.*``
path resolve to the *same* module object. This is required because
``test_research_owner_scope_routes.py`` does a string-targeted
``monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", ...)`` which
must reach the canonical module. This test pins that contract.
"""

import importlib

import routes.research_routes as _shim_research  # noqa: F401


def test_legacy_and_canonical_research_module_are_same_object():
    """``import routes.research_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.research_routes")
    canonical = importlib.import_module("routes.research.research_routes")
    assert legacy is canonical, (
        "routes.research_routes shim must resolve to the canonical "
        "routes.research.research_routes module object"
    )


def test_string_targeted_monkeypatch_reaches_canonical(monkeypatch):
    """String-targeted ``monkeypatch.setattr`` via the legacy path must reach
    the canonical module.

    ``test_research_owner_scope_routes.py`` patches
    ``"routes.research_routes.DEEP_RESEARCH_DIR"`` as an autouse fixture; for
    that to take effect at runtime, the legacy module name and the canonical
    module must be identical.
    """
    legacy = importlib.import_module("routes.research_routes")
    canonical = importlib.import_module("routes.research.research_routes")

    sentinel = "/tmp/shim-test-sentinel"
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", sentinel)
    assert canonical.DEEP_RESEARCH_DIR == sentinel, (
        "string-targeted monkeypatch via legacy path did not reach the canonical module"
    )
    # restore is handled by monkeypatch fixture teardown
    assert legacy is canonical
