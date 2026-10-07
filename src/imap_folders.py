"""Discover IMAP mailboxes without guessing provider-specific wire names.

Names returned by LIST stay in their original wire encoding for SELECT and
APPEND.  Only display_name is decoded from modified UTF-7 for the UI.
"""

import base64
import binascii
import re


_QUOTED = r'"(?:[^"\\]|\\.)*"'
_LIST_LINE = re.compile(
    rf'^\s*(?:\*\s+LIST\s+)?\((?P<flags>[^)]*)\)\s+'
    rf'(?:NIL|{_QUOTED})\s+(?P<name>.+?)\s*$',
    re.IGNORECASE,
)
_ROLE_FLAGS = {
    "\\sent": "sent",
    "\\drafts": "drafts",
    "\\draft": "drafts",  # Older servers use the singular attribute.
    "\\archive": "archive",
    "\\all": "all",
    "\\allmail": "all",  # Legacy XLIST attributes can appear in LIST.
    "\\flagged": "starred",
    "\\starred": "starred",
    "\\junk": "junk",
    "\\spam": "junk",
    "\\trash": "trash",
    "\\important": "important",
    "\\inbox": "inbox",
}
_ROLE_ALIASES = {
    "inbox": ("inbox",),
    "sent": ("sent", "sent mail", "sent items", "sent messages"),
    "drafts": ("drafts", "draft"),
    "archive": ("archive", "archives"),
    "all": ("all mail", "allmail"),
    "starred": ("starred", "flagged"),
    "junk": ("junk", "spam", "junk mail", "junk e-mail"),
    "trash": ("trash", "bin", "deleted messages", "deleted items"),
    "important": ("important",),
}


def _as_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else None


def _display_name(name):
    def decode_run(match):
        encoded = match.group(1)
        if not encoded:
            return "&"
        try:
            raw = encoded.replace(",", "/")
            raw += "=" * (-len(raw) % 4)
            return base64.b64decode(raw, validate=True).decode("utf-16-be")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return match.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", decode_run, name)


def folder_role_from_name(name):
    """Recognize exact conventional names or their hierarchy leaf.

    A label such as "Sent invoices" or "Combine" is an ordinary label, rather
    than Sent or Trash.  Special-use LIST attributes take precedence elsewhere.
    """
    lowered = str(name or "").casefold()
    leaf = re.split(r"[/\.]", lowered)[-1]
    for role, aliases in _ROLE_ALIASES.items():
        if lowered in aliases or leaf in aliases:
            return role
    return ""


def parse_list_response(line):
    """Parse one imaplib LIST response, including a literal mailbox tuple.

    Return None for malformed responses.  The flags are parsed independently
    of the mailbox name so labels containing strings such as "\\Sent" cannot
    impersonate a special-use mailbox.
    """
    literal = None
    if isinstance(line, tuple):
        if len(line) != 2:
            return None
        line, literal = line
    decoded = _as_text(line)
    if decoded is None:
        return None
    match = _LIST_LINE.fullmatch(decoded)
    if not match:
        return None
    raw_name = match.group("name")
    if literal is not None:
        literal_size = re.fullmatch(r"\{(\d+)\+?\}", raw_name)
        name = _as_text(literal)
        if not literal_size or name is None:
            return None
        if isinstance(literal, bytes) and len(literal) != int(literal_size.group(1)):
            return None
    elif raw_name.startswith('"'):
        if not re.fullmatch(_QUOTED, raw_name):
            return None
        name = re.sub(r'\\(["\\])', r"\1", raw_name[1:-1])
    else:
        # Mailbox names can be atoms, quoted strings, or literals.  A standalone
        # literal marker has no mailbox bytes and must not become a folder.
        if re.search(r'[\s(){}"]', raw_name):
            return None
        name = raw_name
    if not name or "\r" in name or "\n" in name or "\x00" in name:
        return None
    flags = match.group("flags").split()
    special_role = next(
        (_ROLE_FLAGS[flag.casefold()] for flag in flags if flag.casefold() in _ROLE_FLAGS),
        "",
    )
    return {
        "name": name,
        "flags": flags,
        "role": special_role or folder_role_from_name(name),
        "display_name": _display_name(name),
    }


def list_folders(conn):
    """Return actual selectable mailboxes and their roles from LIST.

    Failed LIST operations raise; callers can retain a previously discovered
    list, but must not cache an unsuccessful response as an empty success.
    """
    status, lines = conn.list()
    if str(_as_text(status) or "").upper() != "OK":
        raise RuntimeError("IMAP folder listing failed")
    folders = []
    seen = set()
    for line in lines or []:
        folder = parse_list_response(line)
        if folder is None or any(
            flag.casefold() in ("\\noselect", "\\nonexistent") for flag in folder["flags"]
        ):
            continue
        if folder["name"] in seen:
            continue
        seen.add(folder["name"])
        folders.append(folder)
    return folders


def resolve_from_folders(folders, preferred, role=""):
    """Resolve an explicit mailbox or conventional role against real LIST data.

    An exact custom or full preferred name is authoritative.  Bare conventional
    aliases such as "Sent" request a role and prefer special-use attributes.
    Otherwise use conventional aliases that actually occur in LIST.  If no
    discovered folder matches, return preferred for legacy append callers.
    """
    preferred = preferred or ""
    roles = ("archive", "all") if role == "archive" else ((role,) if role else ())
    generic = any(preferred.casefold() in _ROLE_ALIASES.get(wanted, ()) for wanted in roles)
    if not generic:
        for folder in folders:
            if folder["name"] == preferred:
                return preferred
    for wanted in roles:
        for folder in folders:
            if any(_ROLE_FLAGS.get(flag.casefold()) == wanted for flag in folder.get("flags", [])):
                return folder["name"]
    for folder in folders:
        if folder["name"] == preferred:
            return preferred
    # Preserve the case used by the server when an explicit alias differs only
    # in case.  INBOX is case-insensitive in IMAP; other names need not be.
    for folder in folders:
        if folder["name"].casefold() == preferred.casefold() and preferred:
            return folder["name"]
    for wanted in roles:
        for folder in folders:
            if folder.get("role") == wanted or folder_role_from_name(folder["name"]) == wanted:
                return folder["name"]
    return preferred


def resolve_folder(conn, preferred, role=""):
    """Resolve a mailbox, retaining the explicit target if LIST is unavailable.

    Some shared/read-only connections can SELECT a known mailbox while denying
    LIST.  Callers needing an authoritative folder list use list_folders, whose
    failures still propagate instead of becoming a successful empty result.
    """
    try:
        return resolve_from_folders(list_folders(conn), preferred, role)
    except Exception:
        return preferred or ""
