"""Shared test configuration — ensure project root is on sys.path and stub heavy deps."""
import sys
import os
import types
import importlib.util
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _has_module(mod_name: str) -> bool:
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ImportError, ValueError):
        return False


# Stub optional dependencies only when they are not installed. Do not replace
# real FastAPI/Starlette/Pydantic modules: route tests import their subpackages.
for mod_name in [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.types", "sqlalchemy.ext", "sqlalchemy.ext.declarative",
    "sqlalchemy.ext.hybrid", "sqlalchemy.sql", "sqlalchemy.sql.expression",
    "sqlalchemy.sql.sqltypes", "bcrypt", "pyotp",
    "httpx", "fastapi", "fastapi.responses", "fastapi.routing",
    "starlette", "starlette.responses", "starlette.middleware", "starlette.middleware.base",
    "pydantic",
]:
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()

if "src.database" not in sys.modules:
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    _db.Session = MagicMock()
    _db.GalleryImage = MagicMock()
    sys.modules["src.database"] = _db

# Stub core.database ONLY when the real package can't be imported (e.g. deps
# missing). When it is importable we must NOT shadow it: tests such as
# test_session_mode_helpers monkeypatch the real module's SessionLocal and call
# its get/set_session_mode helpers. do_list_sessions only imports the Session
# model class (cheap) and patches get_db_session in its own tests, so the real
# module is safe to use here.
if "core.database" not in sys.modules and not _has_module("core.database"):
    _coredb = types.ModuleType("core.database")
    _coredb.SessionLocal = MagicMock()
    _coredb.Session = MagicMock()
    _coredb.Base = MagicMock()
    sys.modules["core.database"] = _coredb
