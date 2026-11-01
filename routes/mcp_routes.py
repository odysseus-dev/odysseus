"""Backward-compat shim — canonical location is routes/mcp/mcp_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.mcp_routes``, ``from routes.mcp_routes import X``,
``importlib.import_module("routes.mcp_routes")``, the
``sys.modules.pop("routes.mcp_routes")`` + re-import pattern in
test_security_regressions.py, and the ``monkeypatch.setattr(mcp_routes,
"MCP_OAUTH_DIR", ...)`` pattern all operate on the *same* object. This also
makes ``mcp_routes.__file__`` resolve to the canonical file (which the
source-introspection at line 839 reads). Keeps existing import paths working
after slice 2o (#4082/#4071).
"""

import sys as _sys

from routes.mcp import mcp_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
