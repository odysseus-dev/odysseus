"""Backward-compat shim — canonical location is routes/task/task_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.task_routes``, ``from routes.task_routes import X``,
``importlib.import_module("routes.task_routes")``, the
``import ... as task_routes`` + ``monkeypatch.setattr(task_routes,
"SessionLocal", ...)`` / ``"get_current_user"`` pattern used by multiple
tests, and the ``task_routes.__file__`` reads in test_auth_regressions.py
all operate on the *same* object the application actually uses. Keeps
existing import paths working after slice 2p (#4082/#4071).
Source-introspection tests read the canonical file by path.
"""

import sys as _sys

from routes.task import task_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
