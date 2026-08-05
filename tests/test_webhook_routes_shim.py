"""Regression test for the webhook route shim (slice 2l, #4082/#4071)."""

import importlib

import routes.webhook_routes as _shim_webhook  # noqa: F401


def test_legacy_and_canonical_webhook_module_are_same_object():
    legacy = importlib.import_module("routes.webhook_routes")
    canonical = importlib.import_module("routes.webhook.webhook_routes")
    assert legacy is canonical
