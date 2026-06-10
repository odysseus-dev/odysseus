"""Thin re-export shim: the prefs implementation lives in routes/prefs_routes.py
(its canonical upstream home -- tests patch internals there). Service-side callers
import through this module so they need not know about the route layer."""
from routes.prefs_routes import (  # noqa: F401
    PREFS_FILE,
    _load,
    _load_for_user,
    _save,
    _save_for_user,
)
