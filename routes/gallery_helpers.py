"""Backward-compat shim - canonical location is routes/gallery/gallery_helpers.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.gallery_helpers``, ``from routes.gallery_helpers import X``,
``importlib.import_module("routes.gallery_helpers")``, and
``monkeypatch.setattr(routes.gallery_helpers, ...)`` all operate on the same
object. Keeps existing import paths working after slice 2a (#4082/#4071).
"""

import sys as _sys

from routes.gallery import gallery_helpers as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
