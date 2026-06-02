"""Companion system helpers — version checks + DB snapshotting.

Pure, dependency-light functions behind the `/api/companion/system/*` admin
endpoints and the `odysseus-update` CLI. Kept out of routes.py so they can be
unit-tested without a running app (no FastAPI, no network at import time).

Two concerns:
  * update checks — compare the running APP_VERSION against the latest release
    published upstream (GitHub by default; overridable via env).
  * DB extraction — resolve the live SQLite path and copy it out *consistently*
    using SQLite's own `.backup` API, so a running server can't corrupt the
    snapshot mid-write. Same technique as scripts/odysseus-backup.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.request
from pathlib import Path

# ── version comparison ────────────────────────────────────────────────────────

_NUM = re.compile(r"\d+")


def parse_version(value: str | None) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints.

    Tolerant of the shapes release tags actually take: a leading ``v`` is
    dropped, and any pre-release/build suffix (``-rc1``, ``+build5``) is
    ignored so ``1.2.0`` and ``v1.2.0-rc1`` reduce to ``(1, 2, 0)`` and
    ``(1, 2, 0)`` respectively. Non-numeric junk yields an empty tuple, which
    sorts below any real version.
    """
    if not value:
        return ()
    core = str(value).strip().lstrip("vV")
    # Cut anything from the first pre-release/build separator onward.
    core = re.split(r"[-+]", core, maxsplit=1)[0]
    parts = [int(m) for m in _NUM.findall(core)]
    # Trim trailing zeros so (1, 2) == (1, 2, 0) when compared.
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def update_available(current: str | None, latest: str | None) -> bool:
    """True when `latest` is a strictly newer version than `current`.

    Unparseable inputs are treated conservatively: if we can't make sense of
    `latest`, we report no update rather than nagging about a phantom one.
    """
    latest_t = parse_version(latest)
    if not latest_t:
        return False
    return latest_t > parse_version(current)


# ── update-feed fetch ─────────────────────────────────────────────────────────

def fetch_latest_release(url: str, timeout: float = 6.0) -> dict:
    """GET a GitHub-style "latest release" JSON and pull out the bits we show.

    Returns a dict with ``tag``, ``name``, ``html_url`` and ``published_at``
    (any may be None if absent). Raises on network/parse failure — callers wrap
    this so a flaky network degrades to "couldn't check", never a 500.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub's API rejects requests without a User-Agent.
            "User-Agent": "odysseus-companion",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https scheme)
        data = json.loads(resp.read().decode("utf-8"))
    return {
        "tag": data.get("tag_name"),
        "name": data.get("name"),
        "html_url": data.get("html_url"),
        "published_at": data.get("published_at"),
    }


# ── SQLite extraction ─────────────────────────────────────────────────────────

def resolve_sqlite_path(database_url: str, base_dir: str | Path) -> Path | None:
    """Map a SQLAlchemy DATABASE_URL to an on-disk SQLite file path.

    Returns the resolved Path for file-backed SQLite URLs, or None for
    non-SQLite engines (Postgres etc.) and in-memory databases — both of which
    can't be handed back as a single downloadable file. Relative paths resolve
    against `base_dir` (the repo root), matching how the app is launched.
    """
    if not database_url or not database_url.startswith("sqlite"):
        return None
    # Everything after the scheme's `sqlite:///`. A 4th slash means absolute.
    raw = database_url.split("sqlite://", 1)[1].lstrip("/")
    if not raw or raw == ":memory:":
        return None
    # Re-add the leading slash for absolute URLs (sqlite:////abs/path).
    leading = "/" if database_url.startswith("sqlite:////") else ""
    p = Path(leading + raw)
    if not p.is_absolute():
        p = Path(base_dir) / p
    return p.resolve()


def safe_sqlite_snapshot(src: Path, dst: Path) -> None:
    """Copy a live SQLite DB to `dst` via the online `.backup` API.

    Unlike a raw file copy, this is safe while the server is writing: SQLite
    streams a transactionally-consistent image. Mirrors `_sqlite_safe_copy` in
    scripts/odysseus-backup.
    """
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
