"""Turn raw IMAP/SMTP auth errors into actionable messages.

Dependency-free (no SQLAlchemy / pyotp imports) so the mapping can be
unit-tested without standing up the app.

The main case this handles is Microsoft (Outlook / Office 365 / Hotmail /
Live) rejecting plain username+password logins because basic authentication
is disabled tenant-wide. The raw server text ("535 5.7.139 Authentication
unsuccessful, basic authentication is disabled" or a bare "AUTHENTICATE
failed") is opaque to users, who assume the password is wrong and retype it
forever. Odysseus does not implement Microsoft OAuth / Graph yet, so the
honest, actionable answer is: this account cannot be added with a normal
password right now.
"""

# Host substrings that identify a Microsoft-hosted mailbox.
_MICROSOFT_HOSTS = (
    "outlook.",
    "office365.",
    "hotmail.",
    "live.",
    "outlook.office365.com",
    "smtp-mail.outlook.com",
    "outlook.office.com",
)

# Substrings in the raw error that signal a basic-auth / auth rejection.
_BASIC_AUTH_MARKERS = (
    "basic authentication is disabled",
    "5.7.139",
    "authenticate failed",
    "authentication unsuccessful",
    "basic auth",
)

_MICROSOFT_MESSAGE = (
    "Microsoft has disabled basic authentication (username + password) for this "
    "mailbox. Outlook / Office 365 accounts cannot be added with a normal password "
    "yet — Microsoft OAuth / Graph support is not implemented. See README for "
    "current email-provider support."
)


def is_microsoft_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return any(marker in h for marker in _MICROSOFT_HOSTS)


def friendly_auth_error(host: str, raw_error: str) -> str:
    """Return an actionable message for a connection error, or the raw text.

    If the host is Microsoft-hosted and the error looks like a basic-auth
    rejection, return the explanatory Microsoft message. Otherwise return the
    original error unchanged (truncated by the caller as before).
    """
    raw = raw_error or ""
    low = raw.lower()
    if is_microsoft_host(host) and any(m in low for m in _BASIC_AUTH_MARKERS):
        return _MICROSOFT_MESSAGE
    return raw
