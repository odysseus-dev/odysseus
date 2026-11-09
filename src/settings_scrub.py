"""Secret-scrubbing for settings exposed to non-admin / unauthenticated callers.

Deliberately dependency-light (stdlib only) and separate from
``routes/auth_routes.py`` so it can be imported and unit-tested without dragging
in the FastAPI app / auth / database import chain.

``/api/auth/settings`` is auth-exempt, so anyone who can reach the app can call
it. Unauthenticated callers get only :data:`PUBLIC_SETTINGS_KEYS` (an allowlist:
a key added to settings later stays private until someone decides otherwise).
Logged-in non-admins run the whole UI off this endpoint, so they get a
*scrubbed* copy instead. Secrets (provider API keys, IMAP/SMTP passwords, OAuth
tokens) must NOT leak to them — load-bearing when the app is reachable over a
Cloudflare tunnel / reverse proxy. Scrubbing is deep (recurses nested
dicts/lists) and keyed on secret-shaped names.
"""

import re

_SECRET_KEY_PATTERNS = (
    "_api_key", "_apikey", "_password", "_passwd", "_pass", "_pwd",
    "_secret", "_client_secret", "_token", "_access_token", "_refresh_token",
    "_credential", "_credentials", "_key",
)
_SECRET_KEY_ALLOW = ("google_pse_cx",)  # public identifiers, not secrets
_SENSITIVE_KEY_EXACT = (
    # A stable global integration id is a capability handle for routes that can
    # trigger outbound webhook sends; do not expose it to non-admin settings
    # callers even though it is not secret-shaped.
    "reminder_webhook_integration_id",
)


def _canonical_key_name(name: str) -> str:
    """Normalize common JS-style key names so secret matching is style-agnostic."""
    n = (name or "").replace("-", "_")
    n = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", n)
    n = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", n)
    return n.lower()


def is_secret_key(name: str) -> bool:
    n = _canonical_key_name(name)
    if n in _SECRET_KEY_ALLOW:
        return False
    if n in _SENSITIVE_KEY_EXACT:
        return True
    return any(n.endswith(p) or n == p.lstrip("_") for p in _SECRET_KEY_PATTERNS)


def _scrub_value(key, value):
    """Mask secret-shaped leaves, recursing into nested dicts/lists so a secret
    stored under a non-secret parent key (e.g.
    ``{"email_account": {"smtp_password": "..."}}``) is still blanked. Only
    non-empty *string* values are blanked; presence is preserved."""
    if isinstance(value, dict):
        return {
            k: ("" if (is_secret_key(k) and isinstance(v, str) and v)
                else _scrub_value(k, v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(key, item) for item in value]
    if is_secret_key(key) and isinstance(value, str) and value:
        return ""
    return value


def scrub_settings(settings: dict) -> dict:
    """Return a copy of ``settings`` with secret-shaped values masked (deep)."""
    if not isinstance(settings, dict):
        return {}
    return {k: _scrub_value(k, v) for k, v in (settings or {}).items()}


# The only settings an unauthenticated caller may read: keyboard shortcuts and
# text-to-speech preferences, the documented reason the endpoint is auth-exempt.
# Everything else (the reminder ntfy topic, which is a subscribe capability on a
# public ntfy server, the reminder email address, model and endpoint choices)
# needs a session.
PUBLIC_SETTINGS_KEYS = frozenset({
    "keybinds",
    "tts_enabled",
    "tts_provider",
    "tts_model",
    "tts_voice",
    "tts_speed",
})


# Install-wide reminder delivery targets. A logged-in non-admin could subscribe
# to the ntfy topic (it is a capability on a public ntfy server) or learn the
# address the admin's reminders go to; neither can change them (POST
# /api/auth/settings is admin-only), so the non-admin view drops them.
NON_ADMIN_HIDDEN_KEYS = frozenset({
    "reminder_ntfy_topic",
    "reminder_email_to",
})


def non_admin_settings(settings: dict) -> dict:
    """Return the scrubbed copy a logged-in non-admin may read."""
    scrubbed = scrub_settings(settings)
    for key in NON_ADMIN_HIDDEN_KEYS:
        scrubbed.pop(key, None)
    return scrubbed


def public_settings(settings: dict) -> dict:
    """Return only the allowlisted keys of ``settings``, still scrubbed."""
    if not isinstance(settings, dict):
        return {}
    return scrub_settings({k: v for k, v in settings.items() if k in PUBLIC_SETTINGS_KEYS})
