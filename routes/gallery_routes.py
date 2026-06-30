"""Backward-compat shim — canonical location is routes/gallery/gallery_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.gallery_routes``, ``from routes.gallery_routes import X``,
``importlib.import_module("routes.gallery_routes")``, and
``monkeypatch.setattr(routes.gallery_routes, ...)`` all operate on the *same*
object the application actually uses. Keeps existing import paths working
after slice 2a (#4082/#4071). Source-introspection tests read the canonical
file by path.
"""

import sys as _sys

from routes.gallery import gallery_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
