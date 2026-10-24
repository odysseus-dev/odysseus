#!/usr/bin/env python3
"""Build a SQLAlchemy DATABASE_URL from the add-on PostgreSQL options.

Kept separate from run.sh so it is unit-testable and so user/password are
percent-encoded correctly (a password with ``@``, ``/`` or ``:`` would otherwise
corrupt the URL). Reads the ODYSSEUS_DB_* environment variables and prints the
resulting URL on stdout.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote

DEFAULT_PORT = "5432"
DEFAULT_NAME = "odysseus"
DEFAULT_SCHEME = "postgresql+psycopg2"


def build_database_url(
    *,
    host: str,
    port: str = "",
    name: str = "",
    user: str = "",
    password: str = "",
    scheme: str = DEFAULT_SCHEME,
) -> str:
    host = (host or "localhost").strip()
    port = (port or DEFAULT_PORT).strip() or DEFAULT_PORT
    name = (name or DEFAULT_NAME).strip() or DEFAULT_NAME
    user = (user or "").strip()
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    return f"{scheme}://{auth}{host}:{port}/{name}"


def main(argv: list[str]) -> int:
    url = build_database_url(
        host=os.environ.get("ODYSSEUS_DB_HOST", ""),
        port=os.environ.get("ODYSSEUS_DB_PORT", ""),
        name=os.environ.get("ODYSSEUS_DB_NAME", ""),
        user=os.environ.get("ODYSSEUS_DB_USER", ""),
        password=os.environ.get("ODYSSEUS_DB_PASSWORD", ""),
        scheme=os.environ.get("ODYSSEUS_DB_SCHEME", DEFAULT_SCHEME),
    )
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))