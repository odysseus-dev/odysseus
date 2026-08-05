"""Backward-compat shim — canonical location is routes/auth/device_flow.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.device_flow``, ``from routes.device_flow import X``, and
the ``monkeypatch.setattr(device_flow, "require_admin", ...)`` pattern in
test_device_flow_routes.py all operate on the *same* object. Also keeps the
external importers (routes/copilot_routes.py, routes/
chatgpt_subscription_routes.py) working unchanged. Keeps existing import
paths working after slice 2n (#4082/#4071).
"""

import sys as _sys

from routes.auth import device_flow as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
