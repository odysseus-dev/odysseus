"""Regression test for the task route shim (slice 2p, #4082/#4071).

The backward-compat shim at ``routes/task_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.task.*``
path resolve to the *same* module object. This is required because multiple
tests do ``import routes.task_routes as task_routes`` followed by
``monkeypatch.setattr(task_routes, "SessionLocal", ...)`` /
``"get_current_user"``, and test_auth_regressions.py reads
``task_routes.__file__`` for source introspection.
"""

import importlib

import routes.task_routes as _shim_task  # noqa: F401


def test_legacy_and_canonical_task_module_are_same_object():
    legacy = importlib.import_module("routes.task_routes")
    canonical = importlib.import_module("routes.task.task_routes")
    assert legacy is canonical
