# src/app_helpers.py
import base64
import json
import logging
import os

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from starlette.requests import Request

logger = logging.getLogger(__name__)


def _login_theme_json() -> str:
    """Active profile theme as a script-safe JSON literal for the login page.

    The pre-auth login page can't fetch /api/prefs/theme (401) and a fresh
    device has nothing in localStorage, so without this it always renders the
    built-in default. We inject the profile's active theme so login matches
    the theme set in-app. Only a single-user instance is handled — with more
    than one user there's no way to know whose theme to show before sign-in,
    so we return "null" and let the client fall back to localStorage/default.
    Returns the string "null" (a valid JS literal) on any miss or error.
    """
    try:
        from src.constants import USER_PREFS_FILE
        with open(USER_PREFS_FILE, "r", encoding="utf-8") as f:
            users = (json.load(f) or {}).get("_users", {})
        if len(users) != 1:
            return "null"
        theme = next(iter(users.values())).get("theme")
        if not isinstance(theme, dict) or not theme.get("colors"):
            return "null"
        # Escape so the JSON can't break out of the surrounding <script>.
        return (
            json.dumps(theme)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
    except Exception:
        return "null"

def read_if_exists(path: str) -> str:
    """Read file if it exists, return empty string otherwise."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def file_to_data_url(path: str, mime: str) -> str:
    """Convert file to data URL."""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"

def abs_join(base_dir: str, rel: str) -> str:
    """Join paths and return absolute path."""
    return os.path.abspath(os.path.join(base_dir, rel))

def serve_html_with_nonce(request: Request, file_path: str) -> HTMLResponse:
    """Read an app-bundled HTML page and inject the CSP nonce into inline <script> tags.

    Callers pass fixed, server-owned template paths (index/login/backgrounds),
    never a client-supplied path. So any read failure here — a missing file
    (broken deployment) or a permission/IO error — is a server fault, not a
    client "not found": map all of them to a logged 500 so a missing core
    template surfaces in 5xx alerting instead of hiding behind a 404. If a
    future caller serves a client-influenced path where 404 is correct, branch
    that at the call site rather than defaulting this shared helper to 404.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError:
        logger.exception("Failed to read page %s", file_path)
        raise HTTPException(500, "Internal server error")
    nonce = getattr(request.state, "csp_nonce", "")
    html = html.replace("{{CSP_NONCE}}", nonce)
    # Only the login page carries this placeholder; skip the prefs read for
    # every other template (index, backgrounds) that doesn't need it.
    if "{{LOGIN_THEME}}" in html:
        html = html.replace("{{LOGIN_THEME}}", _login_theme_json())
    return HTMLResponse(html)


def inside_base_dir(base_dir: str, path: str) -> bool:
    """Check if path is inside base directory."""
    if not isinstance(base_dir, str) or not isinstance(path, str):
        return False
    base = os.path.realpath(base_dir)
    p = os.path.realpath(path)
    try:
        return os.path.commonpath([base, p]) == base
    except Exception:
        return False
