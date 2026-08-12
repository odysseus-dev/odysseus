"""Backward-compat shim — canonical location is routes/document/document_helpers.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.document_helpers``, ``from routes.document_helpers import
X``, and the ``sys.modules.pop("routes.document_helpers")`` + re-import
pattern used by test_security_regressions.py all operate on the *same* object.
Keeps existing import paths working after slice 2m (#4082/#4071).
"""

import sys as _sys

from routes.document import document_helpers as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
