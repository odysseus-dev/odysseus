"""Backward-compat shim — canonical location is routes/document/document_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.document_routes``, ``from routes.document_routes import
X``, ``importlib.import_module("routes.document_routes")``, and the
``import ... as droutes`` + ``droutes.SessionLocal = ...`` /
``monkeypatch.setattr(droutes, ...)`` pattern used by multiple tests all
operate on the *same* object the application actually uses. Keeps existing
import paths working after slice 2m (#4082/#4071). Source-introspection tests
read the canonical file by path.
"""

import sys as _sys

from routes.document import document_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
