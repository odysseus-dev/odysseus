"""Regression test for the mcp route shim (slice 2o, #4082/#4071).

The backward-compat shim at ``routes/mcp_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.mcp.*``
path resolve to the *same* module object. This is required because
``test_security_regressions.py`` does ``sys.modules.pop("routes.mcp_routes")``
+ re-import, ``monkeypatch.setattr(mcp_routes, "MCP_OAUTH_DIR", ...)``, and
reads ``mcp_routes.__file__`` for source introspection.
"""

import importlib

import routes.mcp_routes as _shim_mcp  # noqa: F401


def test_legacy_and_canonical_mcp_module_are_same_object():
    legacy = importlib.import_module("routes.mcp_routes")
    canonical = importlib.import_module("routes.mcp.mcp_routes")
    assert legacy is canonical
