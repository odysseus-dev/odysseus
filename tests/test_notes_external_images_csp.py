"""CSP side of "Show external images in notes".

The setting is per user and off by default, but the app page's CSP is the
same for everyone: `img-src` already allows `https:` (chat and research render
remote images), and the notes renderer decides per viewer whether an external
image is shown at all (tests/notesExternalImages.test.mjs). These tests pin
the part of that bargain the server owns: https: images are allowed, plain
http: and wildcards are not, the policy does not vary by user, and no
referrer leaves the page.
"""
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware

REPO = Path(__file__).resolve().parent.parent


def _client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    async def index(request: Request, user: str = ""):
        request.state.current_user = user or None
        return {"ok": True}

    return TestClient(app)


def _directive(csp: str, name: str) -> list[str]:
    for part in csp.split(";"):
        tokens = part.split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    raise AssertionError(f"{name} missing from {csp!r}")


def test_app_page_allows_https_images_and_nothing_wider():
    csp = _client().get("/").headers["Content-Security-Policy"]
    img_src = _directive(csp, "img-src")
    assert img_src == ["'self'", "data:", "blob:", "https:"]
    assert "http:" not in img_src and "*" not in img_src


def test_the_policy_is_the_same_for_every_user():
    client = _client()
    nonce = re.compile(r"'nonce-[0-9a-f]+'")
    policies = {
        nonce.sub("'nonce-x'", client.get("/", params={"user": user}).headers["Content-Security-Policy"])
        for user in ("", "alice", "bob")
    }
    assert len(policies) == 1


def test_no_referrer_leaves_the_page():
    assert _client().get("/").headers["Referrer-Policy"] == "no-referrer"


def test_the_setting_ships_off_and_says_what_it_leaks():
    html = (REPO / "static" / "index.html").read_text(encoding="utf-8")
    toggle = re.search(r'<input[^>]*id="set-notes-external-images"[^>]*>', html)
    assert toggle, "the setting's checkbox is in Settings"
    assert "checked" not in toggle.group(0)
    card = html[toggle.end(): toggle.end() + 800]
    assert "IP address" in card
