"""Regression test for the document route shim (slice 2m, #4082/#4071).

The backward-compat shims at ``routes/document_routes.py`` and
``routes/document_helpers.py`` use ``sys.modules`` replacement so the legacy
import paths and the canonical ``routes.document.*`` paths resolve to the
*same* module objects. This is required because multiple tests do
``import routes.document_routes as droutes`` followed by
``droutes.SessionLocal = ...`` / ``monkeypatch.setattr(droutes, ...)`` and
``sys.modules.pop("routes.document_helpers")`` + re-import — for those to
take effect at runtime, the legacy and canonical module objects must be
identical.
"""

import importlib

import routes.document_routes as _shim_routes  # noqa: F401
import routes.document_helpers as _shim_helpers  # noqa: F401


def test_legacy_and_canonical_routes_are_same_object():
    legacy = importlib.import_module("routes.document_routes")
    canonical = importlib.import_module("routes.document.document_routes")
    assert legacy is canonical


def test_legacy_and_canonical_helpers_are_same_object():
    legacy = importlib.import_module("routes.document_helpers")
    canonical = importlib.import_module("routes.document.document_helpers")
    assert legacy is canonical
