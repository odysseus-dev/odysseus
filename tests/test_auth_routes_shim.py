"""Regression test for the auth route shims (slice 2n, #4082/#4071).

The backward-compat shims at ``routes/auth_routes.py``,
``routes/api_token_routes.py``, and ``routes/device_flow.py`` use
``sys.modules`` replacement so the legacy import paths and the canonical
``routes.auth.*`` paths resolve to the *same* module objects. This is
required because:

- ``test_auth_policy.py`` / ``test_auth_session_revocation.py`` do
  ``sys.modules.pop("routes.auth_routes")`` + re-import
- ``test_api_token_routes.py`` does ``monkeypatch.delitem(sys.modules,
  "routes.api_token_routes")`` + re-import
- ``test_rename_user_owner_sync.py`` does ``import ... as ar`` +
  ``monkeypatch.setattr(ar, "DEEP_RESEARCH_DIR", ...)``
- ``test_device_flow_routes.py`` does ``setattr(device_flow,
  "require_admin", ...)``
"""

import importlib

import routes.api_token_routes as _shim_api_token  # noqa: F401
import routes.auth_routes as _shim_auth  # noqa: F401
import routes.device_flow as _shim_device_flow  # noqa: F401


def test_legacy_and_canonical_auth_routes_are_same_object():
    legacy = importlib.import_module("routes.auth_routes")
    canonical = importlib.import_module("routes.auth.auth_routes")
    assert legacy is canonical


def test_legacy_and_canonical_api_token_routes_are_same_object():
    legacy = importlib.import_module("routes.api_token_routes")
    canonical = importlib.import_module("routes.auth.api_token_routes")
    assert legacy is canonical


def test_legacy_and_canonical_device_flow_are_same_object():
    legacy = importlib.import_module("routes.device_flow")
    canonical = importlib.import_module("routes.auth.device_flow")
    assert legacy is canonical
