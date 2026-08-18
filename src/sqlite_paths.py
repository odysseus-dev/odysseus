"""Side-effect-free SQLite URL parsing shared by startup and sandbox policy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy.engine import make_url


def _is_sqlite(parsed_url: Any) -> bool:
    try:
        return parsed_url.get_backend_name() == "sqlite"
    except (AttributeError, TypeError):
        return False


def _query_value(parsed_url: Any, name: str) -> str:
    query = dict(getattr(parsed_url, "query", {}) or {})
    value = query.get(name)
    if isinstance(value, (tuple, list)):
        value = value[-1] if value else ""
    return str(value or "").strip().lower()


def normalize_sqlite_url(url: str, *, app_root: str) -> str:
    """Resolve ordinary relative SQLite paths while preserving URI filenames."""
    try:
        parsed_url = make_url(url)
    except Exception:
        return url

    if not _is_sqlite(parsed_url):
        return url

    database = parsed_url.database
    if (
        not database
        or str(database) == ":memory:"
        or str(database).casefold().startswith("file:")
        or os.path.isabs(str(database))
    ):
        return url

    absolute_path = (Path(app_root) / str(database)).resolve().as_posix()
    return parsed_url.set(database=absolute_path).render_as_string(
        hide_password=False
    )


def sqlite_db_path(parsed_url: Any) -> str | None:
    """Return the path represented by a parsed, file-backed SQLite URL."""
    if not _is_sqlite(parsed_url):
        return None

    database = parsed_url.database
    if not database or str(database) == ":memory:":
        return None

    database = str(database)
    is_file_uri = database.casefold().startswith("file:")
    uri_enabled = _query_value(parsed_url, "uri") in {"1", "true", "yes", "on"}
    if not uri_enabled or not is_file_uri:
        return database

    if (
        database.casefold().startswith("file::memory:")
        or _query_value(parsed_url, "mode") == "memory"
    ):
        return None

    parsed_uri = urlparse(database)
    filesystem_path = parsed_uri.path or ""
    if not filesystem_path or filesystem_path == ":memory:":
        return None

    authority = parsed_uri.netloc
    if authority and authority.casefold() != "localhost":
        filesystem_path = f"//{authority}{filesystem_path}"

    return unquote(filesystem_path)


def resolve_sqlite_db_path(url: str, *, app_root: str) -> str | None:
    """Resolve a file-backed SQLite URL to a canonical path, even if absent."""
    try:
        parsed_url = make_url(url)
    except Exception:
        return None

    database = sqlite_db_path(parsed_url)
    if database is None:
        return None

    path = os.path.expanduser(database)
    if not os.path.isabs(path):
        path = os.path.join(app_root, path)
    return os.path.realpath(os.path.abspath(path))
