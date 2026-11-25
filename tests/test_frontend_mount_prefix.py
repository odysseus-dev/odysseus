"""Guard: frontend URL bases must stay mount-prefix aware.

Under the Home Assistant ingress panel the browser lives at
``/api/hassio_ingress/<token>/``. Any module that builds API URLs from
``window.location.origin`` bypasses that prefix and hits Home Assistant core
instead of Odysseus, which surfaces as 404s ("Session request failed (HTTP
404)") and requests that never resolve (an infinite spinner after sending a
message, because /api/chat_stream never opens).

The single supported source is the bootstrap in static/index.html, which sets
``window.__odysseusAppBase`` to '' for direct access and to the mount prefix
otherwise.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _js_files():
    for path in STATIC.rglob("*.js"):
        parts = set(path.parts)
        if "test-fixtures" in parts or "lib" in parts:
            continue
        yield path


def test_no_module_builds_api_base_from_location_origin():
    offenders = []
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?:const|let|var)\s+\w*API_BASE\w*\s*=\s*window\.location\.origin", text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(STATIC.parent)}:{line}")
    assert not offenders, (
        "These files build API URLs from window.location.origin, which breaks "
        "under the HA ingress panel prefix — use window.__odysseusAppBase:\n  "
        + "\n  ".join(offenders)
    )


def test_index_bootstrap_defines_app_base():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "window.__odysseusAppBase = base;" in html
    assert "window.appUrl = function" in html
    # API_BASE used by chatRenderer must also be the base path.
    assert "window.API_BASE = base;" in html