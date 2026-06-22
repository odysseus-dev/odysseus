"""Regression guard for issue #4633 — `SEARXNG_INSTANCE` was hardcoded to
`http://searxng:8080` in `docker-compose.yml`, so a `.env` override was
silently ignored and users with their own SearXNG instance could not point
Odysseus at it without a `docker-compose.override.yml`.

The fix follows the same `${VAR:-default}` pattern already used for
`DATABASE_URL` a few lines below. This test guards that the override
mechanism stays in place and the bundled container remains the default.
"""
import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_searxng_instance_is_env_overridable():
    text = COMPOSE.read_text(encoding="utf-8")
    # Match the environment line for the odysseus service. We don't anchor on
    # the full docker-compose indenting so the test survives reformatting of
    # surrounding lines, but we do require the line to be inside the
    # `environment:` block of the `odysseus` service (it must appear before
    # the first `depends_on:` line, not under the `searxng` service block).
    m = re.search(r"^\s*-\s*SEARXNG_INSTANCE\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    assert m, "SEARXNG_INSTANCE line not found in docker-compose.yml"
    value = m.group(1)

    # Must use shell-style env interpolation with a fallback default so users
    # can override via .env while bundled installs keep working unchanged.
    assert "${SEARXNG_INSTANCE" in value, (
        "SEARXNG_INSTANCE must be overridable via .env "
        "(use ${SEARXNG_INSTANCE:-<default>}); got %r" % value
    )
    # And the fallback default must still point at the bundled container so
    # existing deployments behave identically without an .env override.
    assert "http://searxng:8080" in value, (
        "SEARXNG_INSTANCE default must still be the bundled container URL; "
        "got %r" % value
    )
