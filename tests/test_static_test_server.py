"""Contract for the loopback static server the browser tests run against.

The server used to bind a fixed port and raise if it was taken, which errored
every collected test rather than the browser ones — so a second worktree
running its own suite took the whole session down with it.
"""

import os
import re
import socket
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_static_origin_is_published_for_node_subprocesses() -> None:
    origin = os.environ.get("ODYSSEUS_TEST_STATIC_ORIGIN")

    assert origin, "the session fixture must publish the origin it bound"
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+", origin)


def test_static_origin_does_not_reuse_the_application_port() -> None:
    """An ephemeral port keeps the suite and a running instance independent."""

    origin = os.environ["ODYSSEUS_TEST_STATIC_ORIGIN"]

    assert not origin.endswith(":7011")


def test_static_server_serves_this_worktree() -> None:
    origin = os.environ["ODYSSEUS_TEST_STATIC_ORIGIN"]
    address = urlsplit(origin)

    # Chromium may open a speculative connection and never send a request;
    # that must not stall the requests queued behind it.
    with socket.create_connection((address.hostname, address.port), timeout=5), \
            urllib.request.urlopen(f"{origin}/static/js/documentStats.js", timeout=5) as r:
        assert r.status == 200
        assert r.headers.get_content_type() == "application/javascript"


def test_no_test_hardcodes_the_static_server_origin() -> None:
    """Regression guard: a hardcoded port reintroduces the collision."""

    needle = "page.goto(" + "'http://127.0.0.1:"
    offenders = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        if needle in path.read_text(encoding="utf-8"):
            offenders.append(path.name)

    assert offenders == [], (
        "browser tests must read ODYSSEUS_TEST_STATIC_ORIGIN rather than a fixed port"
    )
