"""Regression test for the webhook route shim (slice 2l, #4082/#4071).

The backward-compat shim at ``routes/webhook_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.webhook.*``
path resolve to the *same* module object. This is required because
``test_null_owner_gates.py`` uses ``__import__("routes.webhook_routes",
fromlist=["_caller_owns_session"])`` + ``setattr(wh_mod, "ModelEndpoint",
...)`` — for those to take effect at runtime, the legacy module object and
the canonical one must be identical.
"""

import importlib

import routes.webhook_routes as _shim_webhook  # noqa: F401


def test_legacy_and_canonical_webhook_module_are_same_object():
    """``import routes.webhook_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.webhook_routes")
    canonical = importlib.import_module("routes.webhook.webhook_routes")
    assert legacy is canonical, (
        "routes.webhook_routes shim must resolve to the canonical "
        "routes.webhook.webhook_routes module object"
    )
