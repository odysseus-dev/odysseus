"""The outgoing signature block appended to mail sent from an account.

One account, one signature. It is stored as plain text on
``EmailAccount.signature`` and travels through the same body path as
everything the user types, so the markdown renderer that builds the HTML
part renders it too — there is no second formatting path to keep in step.

The delimiter
-------------
RFC 3676 §4.3 defines the signature separator as a line containing exactly
``"-- "`` — two hyphens, a space, nothing else. Receiving clients look for
it to fold the signature away, to keep it out of the quoted text when
someone replies, and to leave it out of a thread summary. Getting the
trailing space right is the whole point: ``"--"`` is just a line of
hyphens, and the signature stops being a signature to every client that
follows the spec.

Placement
---------
In a reply the signature belongs after what the user wrote and *before* the
quoted original — that is where every mail client puts it, and a signature
below a long quote is a signature nobody reads. `apply_signature` finds the
quote boundary and inserts above it, falling back to appending when there
is nothing quoted.

Applying twice
--------------
The composer inserts the signature into the draft so the user can see and
edit it before sending, which means the body reaching the send route
usually already has one. Anything that appends server-side has to notice
that, or a reply ends with the sender's name and phone number twice.
`body_has_signature` is that check, and `apply_signature` makes it for you.
"""

from __future__ import annotations

import re

# RFC 3676 §4.3. The trailing space is load-bearing; see the module docstring.
SIGNATURE_DELIMITER = "-- "

# A signature is a few lines of contact details. The cap exists so a paste
# accident cannot put a novel on the end of every message the account sends.
MAX_SIGNATURE_CHARS = 4000

# Where the quoted original starts in a draft this app built. Both markers
# are emitted by the composer when it builds a reply or a forward.
_QUOTE_MARKER_RE = re.compile(
    r"^-{3,}\s*(?:Previous|Forwarded|Original)\s+[Mm]essage\s*-{3,}\s*$"
)

# Attribution line above a quote, for bodies that came from somewhere else
# ("On Tue, 3 Jun 2026 at 09:14, Ada <ada@example.com> wrote:").
_ATTRIBUTION_RE = re.compile(r"^.*\b(?:wrote|escreveu|schrieb|a écrit)\s*:\s*$")


def normalize_signature(raw) -> str:
    """Clean a signature as typed into the settings field.

    Trailing whitespace goes (it survives a copy-paste and shows up as
    ragged lines in some clients), runs of blank lines collapse, and a
    delimiter the user typed themselves is removed — the send path adds
    exactly one, and two in a row means the second is quoted as content.
    """
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]

    # Drop a leading delimiter, whichever way it was written.
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip() in {"--", "-- "}:
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()

    cleaned: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
        else:
            blanks += 1
            if blanks > 1:
                continue
        cleaned.append(line)
    return "\n".join(cleaned)[:MAX_SIGNATURE_CHARS]


def signature_block(signature) -> str:
    """The delimiter plus the signature, or "" when there is nothing to add."""
    cleaned = normalize_signature(signature)
    if not cleaned:
        return ""
    return f"{SIGNATURE_DELIMITER}\n{cleaned}"


def account_signature(cfg) -> str:
    """The signature to use for a resolved send config, honouring the toggle."""
    cfg = cfg or {}
    if not cfg.get("signature_enabled", True):
        return ""
    return normalize_signature(cfg.get("signature"))


def body_has_signature(body, signature) -> bool:
    """Whether *body* already ends with this signature.

    Compared on stripped lines so a draft the user lightly reflowed still
    counts as signed — the question being answered is "would appending
    duplicate it", not "is it byte-identical".
    """
    cleaned = normalize_signature(signature)
    if not cleaned:
        return False

    def _key(text: str) -> list:
        return [line.strip() for line in text.split("\n") if line.strip()]

    needle = _key(cleaned)
    haystack = _key(str(body or ""))
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i:i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def _quote_start(lines: list) -> int | None:
    """Index of the first line of the quoted original, or None."""
    for idx, line in enumerate(lines):
        if _QUOTE_MARKER_RE.match(line):
            return idx
        if line.startswith(">"):
            # Step back over the attribution line and the blank line above it,
            # so the signature does not land between "…wrote:" and the quote.
            start = idx
            if start and _ATTRIBUTION_RE.match(lines[start - 1]):
                start -= 1
            return start
    return None


def apply_signature(body, signature) -> str:
    """Return *body* with the signature block in place.

    A body that already carries the signature is returned untouched, so this
    is safe to call on a draft the composer already signed.
    """
    block = signature_block(signature)
    if not block:
        return str(body or "")
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    if body_has_signature(text, signature):
        return text

    lines = text.split("\n")
    cut = _quote_start(lines)
    if cut is None:
        return f"{text.rstrip()}\n\n{block}\n" if text.strip() else f"{block}\n"

    above = "\n".join(lines[:cut]).rstrip()
    below = "\n".join(lines[cut:])
    lead = f"{above}\n\n" if above else ""
    return f"{lead}{block}\n\n{below}"
